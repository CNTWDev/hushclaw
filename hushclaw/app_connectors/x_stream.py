"""Background X filtered stream listener.

Uses X API v2 filtered stream over an outbound HTTP connection. This works for
local deployments because HushClaw does not need a public webhook endpoint.
"""
from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.x import API, _bearer, _request
from hushclaw.util.logging import get_logger

log = get_logger("app_connectors.x_stream")

RULE_TAG_PREFIX = "hushclaw:"


def normalize_stream_rules(raw_rules) -> list[dict]:
    rules: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(raw_rules, list):
        return rules
    for idx, item in enumerate(raw_rules):
        if isinstance(item, str):
            value = item.strip()
            tag = f"rule-{idx + 1}"
        elif isinstance(item, dict):
            value = str(item.get("value") or item.get("query") or "").strip()
            tag = str(item.get("tag") or f"rule-{idx + 1}").strip()
        else:
            continue
        if not value:
            continue
        tag = tag.replace("\n", " ").strip()[:80] or f"rule-{idx + 1}"
        key = (value, tag)
        if key in seen:
            continue
        seen.add(key)
        rules.append({"value": value, "tag": f"{RULE_TAG_PREFIX}{tag}"})
    return rules


class XFilteredStreamWorker:
    def __init__(self, config, secrets, memory_store) -> None:
        self.config = config
        self.secrets = secrets
        self.memory_store = memory_store
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def should_start(self) -> bool:
        return bool(
            getattr(self.config, "enabled", False)
            and getattr(self.config, "stream_enabled", False)
            and _bearer(self.config, self.secrets)
            and normalize_stream_rules(getattr(self.config, "stream_rules", []))
            and self.memory_store is not None
        )

    async def start(self) -> None:
        if not self.should_start():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="x-filtered-stream")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 1
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self._sync_rules)
                await asyncio.to_thread(self._consume_stream_once)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stopping.is_set():
                    return
                log.warning("X filtered stream disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _sync_rules(self) -> None:
        token = _bearer(self.config, self.secrets)
        desired = normalize_stream_rules(getattr(self.config, "stream_rules", []))
        if not token or not desired:
            return
        status, payload = _request(token, "/tweets/search/stream/rules")
        if status >= 400:
            raise RuntimeError(f"rules read failed: {status} {payload}")
        current = payload.get("data", []) if isinstance(payload, dict) else []
        desired_keys = {(rule["value"], rule["tag"]) for rule in desired}
        owned = [
            rule
            for rule in current
            if str(rule.get("tag") or "").startswith(RULE_TAG_PREFIX)
        ]
        current_owned_keys = {
            (str(rule.get("value") or ""), str(rule.get("tag") or ""))
            for rule in owned
        }
        owned_ids = [
            str(rule.get("id"))
            for rule in owned
            if rule.get("id") and (str(rule.get("value") or ""), str(rule.get("tag") or "")) not in desired_keys
        ]
        add = [
            {"value": rule["value"], "tag": rule["tag"]}
            for rule in desired
            if (rule["value"], rule["tag"]) not in current_owned_keys
        ]
        actions: dict[str, object] = {}
        if owned_ids:
            actions["delete"] = {"ids": owned_ids}
        if add:
            actions["add"] = add
        if not actions:
            return
        status, payload = _request(token, "/tweets/search/stream/rules", method="POST", data=actions)
        if status >= 400:
            raise RuntimeError(f"rules update failed: {status} {payload}")

    def _consume_stream_once(self) -> None:
        token = _bearer(self.config, self.secrets)
        if not token:
            raise RuntimeError("missing X bearer token")
        params = {
            "tweet.fields": "author_id,created_at,conversation_id,public_metrics",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        url = f"{API}/tweets/search/stream?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "HushClaw-AppConnector/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                for raw_line in resp:
                    if self._stopping.is_set():
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        log.debug("Skipping non-JSON X stream line: %r", line[:120])
                        continue
                    self._store_stream_payload(payload)
        except socket.timeout as exc:
            raise RuntimeError("stream keepalive timeout") from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"stream HTTP {exc.code}: {raw[:300]}") from exc

    def _store_stream_payload(self, payload: dict) -> None:
        tweet = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(tweet, dict):
            return
        tweet_id = str(tweet.get("id") or "").strip()
        if not tweet_id:
            return
        text = str(tweet.get("text") or "")
        matching = payload.get("matching_rules") if isinstance(payload.get("matching_rules"), list) else []
        tags = [str(rule.get("tag") or "") for rule in matching if isinstance(rule, dict)]
        self.memory_store.upsert_app_inbox_event(
            connector_id="x",
            event_type="stream.match",
            external_id=tweet_id,
            title=text[:140],
            body=text,
            source_url=f"https://x.com/i/web/status/{tweet_id}",
            payload={**payload, "matched_rule_tags": tags},
            status="unread",
        )
