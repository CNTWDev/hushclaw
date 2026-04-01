"""X Operator tools for fully automated personal-account operations.

Design goals:
- deterministic tick entry via a fixed task prompt contract
- idempotent action execution (no duplicate replies)
- defensive runtime guards (kill switch, mode, backoff, write budgets)
- official X API adapter with retryable/fatal classification
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hushclaw.tools.base import ToolResult, tool

TRIGGER_PHRASE = "执行 X 运营一轮 tick"
_STATE_FILE = Path("~/.hushclaw/x_operator/state.json").expanduser()
_PROFILE_FILE = Path("~/.hushclaw/x_operator/profile.json").expanduser()
_API_BASE = "https://api.x.com/2"
_TOKEN_ENV = "X_OPERATOR_ACCESS_TOKEN"
_LOCK_TTL_SECONDS = 300
_MAX_ACTION_KEYS = 5000

_DEFAULT_PROFILE = {
    "account_handle": "",
    "persona": "专业、友好、简洁",
    "topics": [],
    "banned_terms": [],
    "reply_style": "concise",
    "max_writes_per_tick": 3,
    "daily_write_cap": 30,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult.ok(json.dumps(payload, ensure_ascii=False, indent=2))


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = dict(fallback)
            merged.update(data)
            return merged
    except Exception:
        pass
    return dict(fallback)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_state() -> dict[str, Any]:
    today = _utc_now().date().isoformat()
    return {
        "mode": "normal",  # normal | quiet | dnd
        "last_mention_id": "",
        "sent_action_keys": [],
        "daily": {"date": today, "writes": 0},
        "backoff_until": "",
        "consecutive_retryable_errors": 0,
        "lock": {},
    }


def _normalize_mode(mode: str) -> str:
    m = mode.strip().lower()
    if m in ("normal", "quiet", "dnd"):
        return m
    return ""


def _load_profile() -> dict[str, Any]:
    return _load_json(_PROFILE_FILE, _DEFAULT_PROFILE)


def _load_state() -> dict[str, Any]:
    return _load_json(_STATE_FILE, _default_state())


def _ensure_daily_counter(state: dict[str, Any]) -> None:
    today = _utc_now().date().isoformat()
    daily = state.get("daily") or {}
    if daily.get("date") != today:
        state["daily"] = {"date": today, "writes": 0}


def _bool_env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on"):
        return True
    return default


def _task_prompt_matches(task_prompt: str) -> bool:
    text = task_prompt.strip()
    if not text:
        return False
    return TRIGGER_PHRASE in text


def _make_action_key(action_type: str, target_id: str) -> str:
    return f"{action_type}:{target_id.strip()}"


def _parse_iso_utc(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _acquire_run_lock(state: dict[str, Any]) -> tuple[bool, str]:
    now = _utc_now()
    lock = state.get("lock") or {}
    expires = _parse_iso_utc(lock.get("expires_at", ""))
    if expires and expires > now:
        return False, lock.get("run_id", "")

    run_id = str(uuid.uuid4())
    state["lock"] = {
        "run_id": run_id,
        "started_at": now.isoformat(),
        "expires_at": (now.timestamp() + _LOCK_TTL_SECONDS),
    }
    # Store expires_at as epoch seconds for simpler cross-language interoperability.
    state["lock"]["expires_at"] = datetime.fromtimestamp(
        state["lock"]["expires_at"], tz=timezone.utc
    ).isoformat()
    return True, run_id


def _release_run_lock(state: dict[str, Any], run_id: str) -> None:
    lock = state.get("lock") or {}
    if lock.get("run_id") == run_id:
        state["lock"] = {}


def _trim_action_keys(keys: list[str]) -> list[str]:
    if len(keys) <= _MAX_ACTION_KEYS:
        return keys
    return keys[-_MAX_ACTION_KEYS:]


def _x_token() -> str:
    return os.environ.get(_TOKEN_ENV, "").strip()


def _classify_http_status(status: int) -> dict[str, Any]:
    if status in (401, 403):
        return {"retryable": False, "fatal": True, "kind": "auth"}
    if status == 429:
        return {"retryable": True, "fatal": False, "kind": "rate_limit"}
    if 500 <= status <= 599:
        return {"retryable": True, "fatal": False, "kind": "server"}
    if 400 <= status <= 499:
        return {"retryable": False, "fatal": True, "kind": "client"}
    return {"retryable": False, "fatal": False, "kind": "ok"}


def _http_json(
    method: str,
    path: str,
    token: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, Any]]:
    if not token:
        return 401, {"error": f"Missing {_TOKEN_ENV}. Configure official X API user token first."}

    url = _API_BASE + path
    if query:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode(query)}"

    payload: bytes | None = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if body is not None:
        payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url=url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return resp.status, {}
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw.strip() else {}
        except Exception:
            return e.code, {"error": raw or str(e)}
    except urllib.error.URLError as e:
        return 599, {"error": f"Network error: {e}"}


def _x_get_me(token: str) -> tuple[int, dict[str, Any]]:
    return _http_json("GET", "/users/me", token, query={"user.fields": "username,name,id"})


def _x_get_mentions(token: str, user_id: str, since_id: str, max_results: int) -> tuple[int, dict[str, Any]]:
    query = {
        "max_results": str(max(5, min(max_results, 100))),
        "tweet.fields": "author_id,conversation_id,created_at,text",
    }
    if since_id:
        query["since_id"] = since_id
    return _http_json("GET", f"/users/{user_id}/mentions", token, query=query)


def _x_reply(token: str, in_reply_to_id: str, text: str) -> tuple[int, dict[str, Any]]:
    body = {
        "text": text,
        "reply": {"in_reply_to_tweet_id": in_reply_to_id},
    }
    return _http_json("POST", "/tweets", token, body=body)


def _compose_reply(mention_text: str, profile: dict[str, Any]) -> str:
    style = str(profile.get("reply_style", "concise")).strip().lower()
    persona = str(profile.get("persona", "友好、专业")).strip()
    trimmed = " ".join((mention_text or "").split())
    if len(trimmed) > 72:
        trimmed = trimmed[:69] + "..."
    if style == "question_first":
        return f"谢谢你提到我。关于“{trimmed}”，你最关注哪一部分？（{persona}）"
    return f"谢谢你提到我。已看到“{trimmed}”，我会给出一个更具体的回应。"


@tool(
    description=(
        "Validate whether a scheduled-task prompt matches the deterministic X operator "
        "trigger contract. The expected phrase is fixed in this skill."
    )
)
def x_operator_validate_task_prompt(task_prompt: str) -> ToolResult:
    text = task_prompt.strip()
    return _json_ok(
        {
            "task_prompt": text,
            "trigger_phrase": TRIGGER_PHRASE,
            "matched": _task_prompt_matches(text),
        }
    )


@tool(
    description=(
        "Save account profile and operator policy for automated X operations. "
        "Use once during setup and update when strategy changes."
    )
)
def x_operator_save_profile(
    account_handle: str = "",
    persona: str = "专业、友好、简洁",
    topics: list[str] | None = None,
    banned_terms: list[str] | None = None,
    reply_style: str = "concise",
    max_writes_per_tick: int = 3,
    daily_write_cap: int = 30,
) -> ToolResult:
    if max_writes_per_tick < 1 or max_writes_per_tick > 20:
        return ToolResult.error("max_writes_per_tick must be in [1, 20]")
    if daily_write_cap < 1 or daily_write_cap > 500:
        return ToolResult.error("daily_write_cap must be in [1, 500]")

    profile = {
        "account_handle": account_handle.strip().lstrip("@"),
        "persona": persona.strip() or _DEFAULT_PROFILE["persona"],
        "topics": [x.strip() for x in (topics or []) if x.strip()],
        "banned_terms": [x.strip().lower() for x in (banned_terms or []) if x.strip()],
        "reply_style": reply_style.strip() or "concise",
        "max_writes_per_tick": max_writes_per_tick,
        "daily_write_cap": daily_write_cap,
        "updated_at": _utc_now().isoformat(),
    }
    _save_json(_PROFILE_FILE, profile)
    return _json_ok({"saved": True, "profile": profile})


@tool(description="Set runtime mode for X operator: normal, quiet, or dnd.")
def x_operator_set_mode(mode: str) -> ToolResult:
    normalized = _normalize_mode(mode)
    if not normalized:
        return ToolResult.error("mode must be one of: normal, quiet, dnd")

    state = _load_state()
    state["mode"] = normalized
    _save_json(_STATE_FILE, state)
    return _json_ok({"updated": True, "mode": normalized})


@tool(description="Get current X operator profile, mode, and runtime counters.")
def x_operator_status() -> ToolResult:
    profile = _load_profile()
    state = _load_state()
    _ensure_daily_counter(state)
    return _json_ok({"profile": profile, "state": state, "trigger_phrase": TRIGGER_PHRASE})


@tool(
    description=(
        "Run one deterministic X operator tick (ingest -> decide -> act). "
        "For scheduled jobs, pass the original task prompt; when it contains the trigger phrase, "
        "this tool executes in full-automation mode with runtime guards."
    )
)
def x_operator_tick(
    task_prompt: str = "",
    dry_run: bool = False,
    max_writes: int = 3,
    mention_batch: int = 20,
) -> ToolResult:
    if task_prompt.strip() and not _task_prompt_matches(task_prompt):
        return ToolResult.error(
            f"Task prompt does not match contract. Expected to contain: {TRIGGER_PHRASE!r}"
        )
    if max_writes < 1 or max_writes > 20:
        return ToolResult.error("max_writes must be in [1, 20]")

    profile = _load_profile()
    state = _load_state()
    _ensure_daily_counter(state)

    if not _bool_env_enabled("X_AUTOMATION_ENABLED", default=True):
        return _json_ok(
            {
                "status": "skipped",
                "reason": "kill_switch",
                "message": "X_AUTOMATION_ENABLED is false; write operations are disabled.",
            }
        )

    mode = _normalize_mode(state.get("mode", "normal")) or "normal"
    if mode == "dnd":
        return _json_ok({"status": "skipped", "reason": "mode_dnd", "mode": mode})

    acquired, run_id = _acquire_run_lock(state)
    if not acquired:
        return _json_ok(
            {"status": "skipped", "reason": "already_running", "active_run_id": run_id, "mode": mode}
        )

    token = _x_token()
    now = _utc_now()
    envelope: dict[str, Any] = {
        "status": "ok",
        "run_id": run_id,
        "started_at": now.isoformat(),
        "mode": mode,
        "dry_run": dry_run,
        "actions_taken": [],
        "skipped": [],
        "errors": [],
        "retryable_count": 0,
        "fatal_count": 0,
    }

    try:
        backoff_until = _parse_iso_utc(state.get("backoff_until", ""))
        if backoff_until and now < backoff_until:
            envelope["status"] = "skipped"
            envelope["reason"] = "backoff_active"
            envelope["backoff_until"] = backoff_until.isoformat()
            return _json_ok(envelope)

        me_status, me_data = _x_get_me(token)
        me_class = _classify_http_status(me_status)
        if me_status != 200:
            envelope["fatal_count"] += 1 if me_class["fatal"] else 0
            envelope["retryable_count"] += 1 if me_class["retryable"] else 0
            envelope["status"] = "fatal" if me_class["fatal"] else "retryable_error"
            envelope["errors"].append(
                {"step": "get_me", "status": me_status, "kind": me_class["kind"], "detail": me_data}
            )
            if me_class["fatal"]:
                state["mode"] = "quiet"
            if me_class["retryable"]:
                state["backoff_until"] = (_utc_now().timestamp() + 300)
                state["backoff_until"] = datetime.fromtimestamp(
                    state["backoff_until"], tz=timezone.utc
                ).isoformat()
            return _json_ok(envelope)

        user_id = ((me_data.get("data") or {}).get("id") or "").strip()
        if not user_id:
            envelope["status"] = "fatal"
            envelope["fatal_count"] += 1
            envelope["errors"].append({"step": "get_me", "detail": "Missing user id in X API response"})
            return _json_ok(envelope)

        mention_status, mention_data = _x_get_mentions(
            token=token,
            user_id=user_id,
            since_id=str(state.get("last_mention_id", "")),
            max_results=mention_batch,
        )
        mention_class = _classify_http_status(mention_status)
        if mention_status != 200:
            envelope["fatal_count"] += 1 if mention_class["fatal"] else 0
            envelope["retryable_count"] += 1 if mention_class["retryable"] else 0
            envelope["status"] = "fatal" if mention_class["fatal"] else "retryable_error"
            envelope["errors"].append(
                {
                    "step": "get_mentions",
                    "status": mention_status,
                    "kind": mention_class["kind"],
                    "detail": mention_data,
                }
            )
            if mention_class["fatal"]:
                state["mode"] = "quiet"
            if mention_class["retryable"]:
                state["backoff_until"] = (_utc_now().timestamp() + 300)
                state["backoff_until"] = datetime.fromtimestamp(
                    state["backoff_until"], tz=timezone.utc
                ).isoformat()
            return _json_ok(envelope)

        mentions = mention_data.get("data") or []
        newest_id = str((mention_data.get("meta") or {}).get("newest_id") or "").strip()
        sent_keys = set(state.get("sent_action_keys") or [])
        banned = set(profile.get("banned_terms") or [])

        writes_remaining_daily = int(profile.get("daily_write_cap", 30)) - int(
            (state.get("daily") or {}).get("writes", 0)
        )
        writes_allowed = min(
            max_writes,
            int(profile.get("max_writes_per_tick", 3)),
            max(0, writes_remaining_daily),
        )
        if mode == "quiet":
            writes_allowed = min(writes_allowed, 1)

        for mention in mentions:
            mention_id = str(mention.get("id") or "").strip()
            if not mention_id:
                continue

            action_key = _make_action_key("reply", mention_id)
            if action_key in sent_keys:
                envelope["skipped"].append({"mention_id": mention_id, "reason": "duplicate"})
                continue

            text = str(mention.get("text") or "")
            lowered = text.lower()
            if any(term and term in lowered for term in banned):
                envelope["skipped"].append({"mention_id": mention_id, "reason": "banned_term"})
                continue

            if writes_allowed <= 0:
                envelope["skipped"].append({"mention_id": mention_id, "reason": "write_budget_exhausted"})
                continue

            reply_text = _compose_reply(text, profile)
            if len(reply_text) > 280:
                reply_text = reply_text[:277] + "..."

            if dry_run:
                envelope["actions_taken"].append(
                    {
                        "type": "reply",
                        "mention_id": mention_id,
                        "text": reply_text,
                        "dry_run": True,
                    }
                )
                writes_allowed -= 1
                continue

            r_status, r_data = _x_reply(token, mention_id, reply_text)
            r_class = _classify_http_status(r_status)
            if r_status in (200, 201):
                new_tweet_id = str((r_data.get("data") or {}).get("id") or "")
                envelope["actions_taken"].append(
                    {
                        "type": "reply",
                        "mention_id": mention_id,
                        "tweet_id": new_tweet_id,
                    }
                )
                sent_keys.add(action_key)
                state["daily"]["writes"] = int(state["daily"]["writes"]) + 1
                writes_allowed -= 1
                continue

            envelope["errors"].append(
                {
                    "step": "reply",
                    "mention_id": mention_id,
                    "status": r_status,
                    "kind": r_class["kind"],
                    "detail": r_data,
                }
            )
            envelope["retryable_count"] += 1 if r_class["retryable"] else 0
            envelope["fatal_count"] += 1 if r_class["fatal"] else 0
            if r_class["fatal"]:
                state["mode"] = "quiet"
            if r_class["retryable"]:
                state["backoff_until"] = (_utc_now().timestamp() + 300)
                state["backoff_until"] = datetime.fromtimestamp(
                    state["backoff_until"], tz=timezone.utc
                ).isoformat()

        if newest_id:
            state["last_mention_id"] = newest_id
        state["sent_action_keys"] = _trim_action_keys(list(sent_keys))

        if envelope["fatal_count"] > 0:
            envelope["status"] = "fatal"
        elif envelope["retryable_count"] > 0:
            envelope["status"] = "retryable_error"
        elif not envelope["actions_taken"]:
            envelope["status"] = "no_op"

        envelope["completed_at"] = _utc_now().isoformat()
        envelope["daily_writes"] = int((state.get("daily") or {}).get("writes", 0))
        envelope["last_mention_id"] = state.get("last_mention_id", "")
        return _json_ok(envelope)
    finally:
        _release_run_lock(state, run_id)
        _save_json(_STATE_FILE, state)
