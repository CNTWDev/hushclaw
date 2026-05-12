"""Knowledge Hub HTTP route handlers (team distro only)."""
from __future__ import annotations

import json
from urllib.parse import unquote_plus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.os_api import AgentOSService

_HUB_SCOPE = "shared"
_BROADCAST_SCOPE = "org_broadcast"


def register_hub_routes(os_api: "AgentOSService", token: str) -> None:
    """Register /knowledge/* on the os_api HTTP handler registry (API port)."""
    mem = os_api.memory_port()

    async def handler(
        method: str,
        path: str,
        query: str,
        headers: dict,
        body: bytes,
        auth: str,
    ) -> tuple[int, bytes]:
        if method == "POST":
            if token:
                expected = f"Bearer {token}"
                if auth != expected:
                    return 401, json.dumps({"error": "unauthorized"}).encode()

        if path == "/knowledge/search":
            params = _parse_qs(query)
            q = params.get("q", "").strip()
            scope = params.get("scope", _HUB_SCOPE)
            limit = int(params.get("limit", "20"))
            try:
                from hushclaw.runtime.principal import SINGLE_USER_PRINCIPAL
                results = mem.search(q, scopes=[scope], principal=SINGLE_USER_PRINCIPAL, limit=limit)
            except Exception as exc:
                return 500, json.dumps({"error": str(exc)}).encode()
            return 200, json.dumps({"results": results}).encode()

        if path == "/knowledge/promote" and method == "POST":
            data = json.loads(body.decode()) if body else {}
            content = (data.get("content") or "").strip()
            scope = data.get("scope") or _HUB_SCOPE
            if not content:
                return 400, json.dumps({"error": "content is required"}).encode()
            try:
                from hushclaw.runtime.principal import SINGLE_USER_PRINCIPAL
                note_id = mem.remember(
                    content,
                    scope=scope,
                    principal=SINGLE_USER_PRINCIPAL,
                    metadata=data.get("metadata"),
                )
            except Exception as exc:
                return 500, json.dumps({"error": str(exc)}).encode()
            return 200, json.dumps({"id": note_id}).encode()

        if path == "/knowledge/broadcast" and method == "POST":
            data = json.loads(body.decode()) if body else {}
            items = data.get("items") or ([data] if data.get("content") else [])
            if not items:
                return 400, json.dumps({"error": "items or content is required"}).encode()
            ids = []
            from hushclaw.runtime.principal import SINGLE_USER_PRINCIPAL
            for item in items:
                content = (item.get("content") or "").strip()
                if content:
                    note_id = mem.remember(
                        content,
                        scope=_BROADCAST_SCOPE,
                        principal=SINGLE_USER_PRINCIPAL,
                        metadata=item.get("metadata"),
                    )
                    ids.append(note_id)
            return 200, json.dumps({"ids": ids, "count": len(ids)}).encode()

        return 404, json.dumps({"error": "not found"}).encode()

    os_api.register_http_handler("/knowledge/", handler)


def _parse_qs(query: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[unquote_plus(k)] = unquote_plus(v)
    return result
