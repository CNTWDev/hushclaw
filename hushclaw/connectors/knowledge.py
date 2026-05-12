"""KnowledgeConnector — federated team knowledge hub client.

Connects a personal HushClaw instance to a remote Knowledge Hub over HTTP.
The Hub is a completely separate deploy unit (e.g., another HushClaw instance
running --distro team, or any service exposing the /knowledge/* endpoints).

Key design principles:
- Personal memory.db is NEVER exposed to the Hub.
- Sharing is always an explicit user action (promote / write_shared).
- This connector only adds knowledge as an *optional* augmentation source.
- If the Hub is down, personal recall works normally (graceful degradation).

API contract (Hub must implement):
  GET  /knowledge/search?q=<query>&scope=<scope>&limit=<n>
       → {"results": [{"note_id": str, "title": str, "body": str, "scope": str}]}
  POST /knowledge/share
       {content, title, tags, scope, source_principal_id}
       → {"note_id": str}
  GET  /knowledge/policy
       → {"policies": [{"scope": str, "readable_by": list[str], "writable_by": list[str]}]}
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.config.schema import KnowledgeHubConfig

log = get_logger("connectors.knowledge")


class KnowledgeConnector:
    """HTTP client for a remote Knowledge Hub.

    Not a Connector subclass — it does not route messages, it exposes
    a query/share API that tools call directly via _knowledge_hub injection.
    """

    def __init__(self, config: "KnowledgeHubConfig") -> None:
        self._config = config
        self._base_url = config.url.rstrip("/")
        self._token = config.token
        self._cache: dict[str, tuple[float, list[dict]]] = {}  # key → (ts, results)
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._config.enabled or not self._base_url:
            return
        log.info("[knowledge] Hub connector enabled: %s (team_scope=%r)", self._base_url, self._config.team_scope)
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._cache.clear()

    @property
    def connected(self) -> bool:
        return self._running and bool(self._base_url)

    # ── Public API (called by tools / os_api) ─────────────────────────────────

    async def read_shared(
        self,
        query: str,
        *,
        scope: str = "",
        limit: int = 5,
    ) -> list[dict]:
        """Search the Hub for shared knowledge. Returns list of {note_id, title, body, scope}."""
        if not self.connected:
            return []
        scope = scope or self._config.team_scope or ""
        cache_key = f"{query}|{scope}|{limit}"
        ttl = self._config.cache_ttl_seconds
        if ttl > 0 and cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if time.time() - ts < ttl:
                return cached

        params = {"q": query, "limit": str(limit)}
        if scope:
            params["scope"] = scope
        url = f"{self._base_url}/knowledge/search?{urllib.parse.urlencode(params)}"
        try:
            data = await asyncio.get_event_loop().run_in_executor(None, self._get, url)
            results = data.get("results") or []
            if ttl > 0:
                self._cache[cache_key] = (time.time(), results)
            return results
        except Exception as exc:
            log.warning("[knowledge] read_shared failed (Hub may be down): %s", exc)
            return []

    async def write_shared(
        self,
        content: str,
        *,
        title: str = "",
        tags: list[str] | None = None,
        scope: str = "",
        source_principal_id: str = "local-user",
    ) -> str | None:
        """Promote a note to the Hub. Returns hub note_id or None on failure."""
        if not self.connected:
            log.warning("[knowledge] write_shared called but Hub connector is not connected")
            return None
        scope = scope or self._config.team_scope or "team:shared"
        payload = {
            "content": content,
            "title": title,
            "tags": tags or [],
            "scope": scope,
            "source_principal_id": source_principal_id,
        }
        url = f"{self._base_url}/knowledge/share"
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None, self._post, url, payload
            )
            note_id = data.get("note_id", "")
            log.info("[knowledge] shared note to Hub: note_id=%s scope=%s", note_id[:8], scope)
            self._cache.clear()  # invalidate search cache after write
            return note_id or None
        except Exception as exc:
            log.warning("[knowledge] write_shared failed: %s", exc)
            return None

    async def sync_policy(self) -> list[dict]:
        """Fetch current access policies from Hub. Returns list of policy dicts."""
        if not self.connected:
            return []
        url = f"{self._base_url}/knowledge/policy"
        try:
            data = await asyncio.get_event_loop().run_in_executor(None, self._get, url)
            return data.get("policies") or []
        except Exception as exc:
            log.warning("[knowledge] sync_policy failed: %s", exc)
            return []

    # ── Internal HTTP helpers (stdlib only — zero dependencies) ───────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def _post(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
