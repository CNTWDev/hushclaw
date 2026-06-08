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

from hushclaw.app_connectors.x import API, _bearer, _request, _request_user_context
from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context

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
        self._account_profile: dict | None = None

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
            "tweet.fields": "author_id,created_at,conversation_id,public_metrics,referenced_tweets",
            "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields": "username,name",
        }
        url = f"{API}/tweets/search/stream?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "HushClaw-AppConnector/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=35, context=make_ssl_context()) as resp:
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
        tags_raw = [str(rule.get("tag") or "") for rule in matching if isinstance(rule, dict)]
        tags = []
        for tag in tags_raw:
            clean = tag[len(RULE_TAG_PREFIX):] if tag.startswith(RULE_TAG_PREFIX) else tag
            clean = clean.strip()
            if clean and clean not in tags:
                tags.append(clean)
        normalized = self._normalize_inbound_payload(payload, tags)
        self.memory_store.upsert_app_inbox_event(
            connector_id="x",
            event_type="inbound.message",
            external_id=tweet_id,
            title=text[:140],
            body=text,
            source_url=f"https://x.com/i/web/status/{tweet_id}",
            payload=normalized,
            status="unread",
        )

    def _normalize_inbound_payload(self, payload: dict, tags: list[str]) -> dict:
        tweet = payload.get("data") if isinstance(payload, dict) else {}
        tweet = tweet if isinstance(tweet, dict) else {}
        includes = payload.get("includes") if isinstance(payload, dict) else {}
        includes = includes if isinstance(includes, dict) else {}
        users = includes.get("users") if isinstance(includes.get("users"), list) else []
        tweets = includes.get("tweets") if isinstance(includes.get("tweets"), list) else []
        user_map = {
            str(item.get("id") or ""): item
            for item in users
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        tweet_map = {
            str(item.get("id") or ""): item
            for item in tweets
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        author_id = str(tweet.get("author_id") or "").strip()
        author = user_map.get(author_id, {})
        author_username = str(author.get("username") or "").strip()
        referenced = tweet.get("referenced_tweets") if isinstance(tweet.get("referenced_tweets"), list) else []
        reply_target_id = ""
        reply_target_author_id = ""
        for item in referenced:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip() != "reply":
                continue
            reply_target_id = str(item.get("id") or "").strip()
            if reply_target_id:
                reply_target_author_id = str((tweet_map.get(reply_target_id) or {}).get("author_id") or "").strip()
                break
        account = self._load_account_profile()
        account_id = str(account.get("id") or "").strip()
        account_username = str(account.get("username") or "").strip().lower()
        text = str(tweet.get("text") or "")
        text_lower = text.lower()
        normalized_event_type = "keyword_match"
        if reply_target_id and account_id and reply_target_author_id == account_id:
            normalized_event_type = "reply_to_me"
        elif account_username and f"@{account_username}" in text_lower and author_id != account_id:
            normalized_event_type = "mention"
        elif reply_target_id:
            normalized_event_type = "thread_reply"
        return {
            **payload,
            "direction": "inbound",
            "thread_id": str(tweet.get("conversation_id") or tweet.get("id") or "").strip(),
            "author_external_id": author_id,
            "author_username": author_username,
            "target_external_id": reply_target_id,
            "matched_rule_tags": tags,
            "matched_rule_tags_raw": [
                str(rule.get("tag") or "")
                for rule in (payload.get("matching_rules") or [])
                if isinstance(rule, dict) and str(rule.get("tag") or "").strip()
            ],
            "normalized_event_type": normalized_event_type,
            "account_user_id": account_id,
            "account_username": account_username,
        }

    def _load_account_profile(self) -> dict:
        if isinstance(self._account_profile, dict):
            return self._account_profile
        result = _request_user_context(
            self.config,
            self.secrets,
            "/users/me?user.fields=username,name",
        )
        if not isinstance(result, tuple):
            self._account_profile = {}
            return self._account_profile
        status, payload = result
        if status >= 400 or not isinstance(payload, dict):
            self._account_profile = {}
            return self._account_profile
        user = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        self._account_profile = {
            "id": str(user.get("id") or "").strip(),
            "username": str(user.get("username") or "").strip(),
        }
        return self._account_profile
