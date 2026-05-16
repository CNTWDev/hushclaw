"""HushClaw WebSocket server — requires 'websockets>=12.0' (pip install hushclaw[server]).

This module is the slim core: constants, session types, and the HushClawServer class
that ties together the mixin modules from hushclaw/server/.

Domain logic is split across:
  server/session.py     — _SessionEntry, _SessionSink, session constants
  server/memory_mixin.py — memory/note helpers, compact_auto_memories
  server/http_mixin.py  — HTTP handler, file serving, upload, config watcher
  server/config_mixin.py — config status/apply, playwright, models, handler delegators
  server/chat_mixin.py  — chat/pipeline/orchestrate flows, attachments, skills
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from hushclaw.config.schema import ServerConfig
from hushclaw.memory.kinds import ALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
from hushclaw.memory.taxonomy import (
    classify_belief_model,
    classify_note,
    classify_profile_fact,
    classify_reflection,
    context_taxonomy,
)
from hushclaw.runtime.principal import RuntimePrincipal, principal_context
from hushclaw.os_api import AgentOSService
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger
from hushclaw.update import UpdateExecutor, UpdateService
from hushclaw.server.session import _SessionEntry, _SessionSink, _SESSION_TTL
from hushclaw.server.memory_mixin import MemoryMixin
from hushclaw.server.http_mixin import HttpMixin
from hushclaw.server.config_mixin import ConfigMixin
from hushclaw.server.chat_mixin import ChatMixin
from hushclaw.server.calendar_mixin import CalendarMixin

log = get_logger("server")


def _request_api_key(ws) -> str:
    """Read API key from WS header first, then URL query (?api_key=...)."""
    try:
        key = ws.request.headers.get("X-API-Key", "")
        if key:
            return key
    except Exception:
        pass

    # Browser WebSocket APIs can't set custom headers, so allow query param fallback.
    try:
        raw_path = getattr(ws.request, "path", "") or ""
        query = urlparse(raw_path).query
        return parse_qs(query).get("api_key", [""])[0]
    except Exception:
        return ""


class HushClawServer(MemoryMixin, HttpMixin, ConfigMixin, ChatMixin, CalendarMixin):
    """
    WebSocket server that exposes the Gateway via a JSON protocol.

    Wire Protocol
    -------------
    Client → Server:
      {"type": "chat",        "text": "...", "agent": "default", "session_id": "s-xxx"}
      {"type": "pipeline",    "text": "...", "agents": ["a1","a2"], "session_id": "s-xxx"}
      {"type": "orchestrate", "text": "...", "session_id": "s-xxx"}
      {"type": "ping"}

    Server → Client (streaming):
      {"type": "session",        "session_id": "s-xxx"}
      {"type": "chunk",          "text": "Hello"}
      {"type": "tool_call",      "tool": "remember", "input": {...}}
      {"type": "tool_result",    "tool": "remember", "result": "Saved: abc12345"}
      {"type": "pipeline_step",  "agent": "writer",  "output": "..."}
      {"type": "done",           "text": "<full response>", "input_tokens": 100, "output_tokens": 50}
      {"type": "error",          "message": "..."}
      {"type": "pong"}
    """

    @staticmethod
    def _clean_optional_text(value) -> str | None:
        """Normalize optional text fields from WebSocket payloads."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    async def _send_json(self, ws, payload: dict, *, default=str) -> None:
        """Thin wrapper around JSON WebSocket replies."""
        await ws.send(json.dumps(payload, default=default))

    def _os(self) -> AgentOSService:
        os_api = getattr(self, '_os_api', None)
        return os_api if os_api is not None else AgentOSService(self._gateway)

    async def _send_enterprise_required(self, ws, request_type: str) -> None:
        await ws.send(json.dumps({
            "type": "error",
            "request_type": request_type,
            "message": "enterprise distro required",
        }))

    # ── Session query handlers ─────────────────────────────────────────────────

    async def _handle_list_sessions(self, ws, data: dict) -> None:
        gw_cfg = self._gateway.base_agent.config.gateway
        limit = int(data.get("limit", gw_cfg.session_list_limit))
        offset = max(0, int(data.get("offset", 0)))
        include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
        max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
        ws_raw = data.get("workspace")
        workspace_filter = None if ws_raw is None else str(ws_raw).strip()
        fetch_limit = limit + 1  # fetch one extra to detect has_more
        items, has_more = self._os().list_sessions(
            limit=limit,
            offset=offset,
            include_scheduled=bool(include_scheduled),
            max_idle_days=max(0, max_idle_days),
            workspace=workspace_filter,
        )
        await self._send_json(ws, {
            "type": "sessions",
            "items": items,
            "offset": offset,
            "has_more": has_more,
        })

    async def _handle_get_session_history(self, ws, data: dict) -> None:
        sid = data.get("session_id", "")
        history = self._os().session_history(sid)
        await self._send_json(ws, {
            "type": "session_history",
            "session_id": sid,
            "turns": history["turns"],
            "summary": history["summary"],
            "lineage": history["lineage"],
        })

    async def _handle_search_sessions(self, ws, data: dict) -> None:
        query = data.get("query", "")
        limit = int(data.get("limit", 20))
        include_scheduled = bool(data.get("include_scheduled", True))
        ws_raw = data.get("workspace")
        workspace_filter = None if ws_raw is None else str(ws_raw).strip()
        items = self._os().search_sessions(
            query=query,
            limit=max(1, limit),
            include_scheduled=include_scheduled,
            workspace=workspace_filter,
        )
        await self._send_json(ws, {
            "type": "session_search_results",
            "query": query,
            "items": items,
        })

    @staticmethod
    def _briefing_session_source(item: dict) -> dict:
        return {
            "type": "session",
            "id": str(item.get("session_id") or ""),
            "title": str(item.get("title") or item.get("last_preview") or "Untitled session"),
            "updated": int(item.get("last_turn") or item.get("updated") or 0),
        }

    @staticmethod
    def _briefing_todo_source(item: dict) -> dict:
        return {
            "type": "todo",
            "id": str(item.get("todo_id") or ""),
            "title": str(item.get("title") or "Todo"),
            "updated": int(item.get("updated") or item.get("created") or 0),
        }

    def _workspace_label(self, workspace: str) -> str:
        return workspace or "Default"

    async def _handle_get_workspace_briefing(self, ws, data: dict) -> None:
        workspace = str(data.get("workspace") or "").strip()
        now = int(time.time())

        briefing_inputs = self._os().workspace_briefing_inputs(workspace=workspace)
        sessions = briefing_inputs["sessions"]
        todos = briefing_inputs["todos"]
        scheduled = briefing_inputs["scheduled"]
        reflections = briefing_inputs["reflections"]

        priority_todos = [t for t in todos if int(t.get("priority") or 0)]
        due_todos = [t for t in todos if t.get("due_at") and int(t.get("due_at") or 0) <= now + 86400]
        enabled_scheduled = [t for t in scheduled if int(t.get("enabled", 1))]
        failures = [r for r in reflections if not bool(r.get("success"))]

        focus_items: list[dict] = []
        for item in sessions[:3]:
            focus_items.append({
                "title": item.get("title") or item.get("last_preview") or "Recent session",
                "detail": item.get("last_preview") or item.get("title") or "",
                "source": self._briefing_session_source(item),
            })
        for item in priority_todos[:2]:
            focus_items.append({
                "title": item.get("title") or "High priority todo",
                "detail": "High priority todo",
                "source": self._briefing_todo_source(item),
            })

        risks: list[dict] = []
        if due_todos:
            risks.append({
                "title": f"{len(due_todos)} todo(s) due soon",
                "detail": "Review pending commitments before starting new work.",
                "severity": "medium",
            })
        if failures:
            risks.append({
                "title": "Recent failed reflection detected",
                "detail": failures[0].get("lesson") or failures[0].get("outcome") or "Review the latest failed task before repeating the workflow.",
                "severity": "medium",
            })
        if not sessions and not todos:
            risks.append({
                "title": "No active workspace signal yet",
                "detail": "Start a conversation or add todos so HushClaw can build a sharper briefing.",
                "severity": "low",
            })

        suggestions: list[dict] = []
        if sessions:
            latest = sessions[0]
            title = latest.get("title") or latest.get("last_preview") or "latest work"
            suggestions.append({
                "id": "continue-" + str(latest.get("session_id") or make_id())[:12],
                "type": "continue_work",
                "title": "Continue recent work",
                "body": f"Pick up from: {title}",
                "action": "chat_prompt",
                "prompt": f"Continue the recent workspace thread: {title}. Summarize the current state, identify the next concrete step, then proceed.",
                "sources": [self._briefing_session_source(latest)],
            })
        if priority_todos:
            todo = priority_todos[0]
            suggestions.append({
                "id": "todo-focus-" + str(todo.get("todo_id") or make_id())[:12],
                "type": "review_risk",
                "title": "Focus high-priority todo",
                "body": todo.get("title") or "A high-priority todo is still open.",
                "action": "chat_prompt",
                "prompt": f"Help me make progress on this high-priority todo: {todo.get('title')}. Start by proposing a short execution plan.",
                "sources": [self._briefing_todo_source(todo)],
            })
        if not todos and sessions:
            latest = sessions[0]
            suggestions.append({
                "id": "create-followup-" + str(latest.get("session_id") or make_id())[:12],
                "type": "create_todo",
                "title": "Capture a follow-up todo",
                "body": "Turn the latest thread into one concrete follow-up item.",
                "action": "create_todo",
                "todo": {
                    "title": "Follow up: " + (latest.get("title") or latest.get("last_preview") or "recent HushClaw thread")[:80],
                    "notes": "Created from proactive workspace briefing.",
                    "priority": 0,
                    "tags": ["briefing"],
                },
                "sources": [self._briefing_session_source(latest)],
            })
        if not enabled_scheduled:
            suggestions.append({
                "id": f"schedule-briefing-{workspace or 'default'}",
                "type": "schedule_followup",
                "title": "Create a daily workspace briefing",
                "body": "Schedule a morning review so this workspace starts with context.",
                "action": "chat_prompt",
                "prompt": "Help me create a daily scheduled task that generates a concise workspace briefing every morning.",
                "sources": [],
            })

        await self._send_json(ws, {
            "type": "workspace_briefing",
            "workspace": workspace,
            "created_at": now,
            "summary": (
                f"{self._workspace_label(workspace)} has {len(sessions)} recent session(s), "
                f"{len(todos)} pending todo(s), and {len(enabled_scheduled)} active scheduled task(s)."
            ),
            "focus_items": focus_items[:5],
            "risks": risks[:4],
            "suggestions": suggestions[:5],
            "sources": {
                "sessions": [self._briefing_session_source(s) for s in sessions[:5]],
                "todos": [self._briefing_todo_source(t) for t in todos[:5]],
            },
        })

    async def _handle_accept_briefing_suggestion(self, ws, data: dict) -> None:
        action = str(data.get("action") or "").strip()
        suggestion_id = str(data.get("suggestion_id") or "").strip()
        if action == "create_todo":
            todo = data.get("todo") if isinstance(data.get("todo"), dict) else {}
            item = self._os().accept_briefing_create_todo(
                todo,
                fallback_title=str(data.get("title") or "Briefing follow-up"),
            )
            await self._send_json(ws, {
                "type": "briefing_suggestion_accepted",
                "suggestion_id": suggestion_id,
                "action": action,
                "ok": True,
                "item": item,
            })
            await self._send_json(ws, {"type": "todo_created", "item": item})
            return
        await self._send_json(ws, {
            "type": "briefing_suggestion_accepted",
            "suggestion_id": suggestion_id,
            "action": action,
            "ok": action == "chat_prompt",
            "prompt": data.get("prompt", ""),
        })

    async def _handle_get_session_lineage(self, ws, data: dict) -> None:
        sid = data.get("session_id", "")
        items = self._os().session_lineage(sid)
        await self._send_json(ws, {
            "type": "session_lineage",
            "session_id": sid,
            "items": items,
        })

    async def _handle_get_learning_state(self, ws, data: dict) -> None:
        os_svc = self._os()
        state = os_svc.learning_state(
            reflection_limit=int(data.get("reflection_limit", 8) or 8),
            skill_outcome_limit=int(data.get("skill_outcome_limit", 10) or 10),
        )
        await self._send_json(ws, {
            "type": "learning_state",
            "profile_snapshot": state["profile_snapshot"],
            "profile_text": state["profile_text"],
            "reflections": [
                self._reflection_payload(os_svc, item)
                for item in state["reflections"]
            ],
            "skill_outcomes": state["skill_outcomes"],
        })

    @staticmethod
    def _profile_fact_value(item: dict) -> str:
        raw = item.get("value_json") or {}
        if isinstance(raw, dict):
            return str(raw.get("summary") or raw.get("value") or json.dumps(raw, ensure_ascii=False))
        return str(raw or "")

    @staticmethod
    def _format_task_fingerprint(value: str) -> str:
        raw = str(value or "general_assistance").strip() or "general_assistance"
        return " ".join(part.capitalize() for part in raw.split("_") if part)

    @staticmethod
    def _session_source_payload(os_svc: Any, session_id: str) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        try:
            brief = os_svc.get_session_brief(sid)
        except Exception:
            brief = None
        brief = brief or {"session_id": sid}
        return {
            "type": "session",
            "session_id": sid,
            "title": brief.get("title") or f"Session {sid[-8:]}",
            "kind": brief.get("kind", ""),
            "workspace": brief.get("workspace", ""),
            "last_turn": int(brief.get("last_turn") or 0),
            "turn_count": int(brief.get("turn_count") or 0),
        }

    @staticmethod
    def _note_source_payload(os_svc: Any, note_id: str) -> dict | None:
        nid = str(note_id or "").strip()
        if not nid:
            return None
        try:
            note = os_svc.get_note(nid)
        except Exception:
            note = None
        if not note:
            return {"type": "note", "note_id": nid, "title": f"Memory {nid[-8:]}"}
        return {
            "type": "note",
            "note_id": nid,
            "title": note.get("title") or f"Memory {nid[-8:]}",
            "note_type": note.get("note_type", ""),
            "memory_kind": note.get("memory_kind", ""),
            "created": int(note.get("created") or 0),
            "updated": int(note.get("modified") or 0),
        }

    @classmethod
    def _profile_fact_payload(cls, os_svc: Any, fact: dict) -> dict:
        return {
            "fact_id": fact.get("fact_id", ""),
            "category": fact.get("category", ""),
            "key": fact.get("key", ""),
            "value": cls._profile_fact_value(fact),
            "confidence": float(fact.get("confidence") or 0.0),
            "updated": int(fact.get("updated") or 0),
            "source": cls._session_source_payload(os_svc, fact.get("source_session_id", "")),
            **classify_profile_fact(fact),
        }

    @classmethod
    def _belief_payload(cls, os_svc: Any, belief: dict) -> dict:
        out = dict(belief)
        entries = []
        for entry in (belief.get("entries") or [])[:10]:
            item = dict(entry)
            item["source"] = cls._note_source_payload(os_svc, item.get("note_id", ""))
            entries.append(item)
        out["entries"] = entries
        out["entry_count"] = len(belief.get("entries") or [])
        out["display_domain"] = (
            "Unclassified Signals" if str(out.get("domain") or "") == "general" else out.get("domain", "")
        )
        out.update(classify_belief_model(out))
        return out

    @classmethod
    def _reflection_payload(cls, os_svc: Any, reflection: dict) -> dict:
        out = dict(reflection)
        out["source"] = cls._session_source_payload(os_svc, reflection.get("session_id", ""))
        out.update(classify_reflection(reflection))
        return out

    async def _handle_get_memory_overview(self, ws, data: dict) -> None:
        os_svc = self._os()
        overview = os_svc.memory_overview(
            session_id=data.get("session_id", "") or "",
            reflection_limit=int(data.get("reflection_limit", 30) or 30),
        )
        profile_facts = overview["profile_facts"]
        profile_by_category: dict[str, int] = {}
        for fact in profile_facts:
            category = str(fact.get("category") or "misc")
            profile_by_category[category] = profile_by_category.get(category, 0) + 1
        high_confidence = sorted(
            profile_facts,
            key=lambda f: (float(f.get("confidence") or 0.0), int(f.get("updated") or 0)),
            reverse=True,
        )[:18]

        beliefs = overview["beliefs"]
        reflections = overview["reflections"]
        recent_notes = []
        for n in overview["recent_notes"]:
            normalized = self._normalize_note_payload(n)
            recent_notes.append({**normalized, **classify_note(normalized)})
        working_state = overview["working_state"]

        task_counts: dict[str, int] = {}
        for r in reflections:
            label = self._format_task_fingerprint(r.get("task_fingerprint", ""))
            task_counts[label] = task_counts.get(label, 0) + 1

        await self._send_json(ws, {
            "type": "memory_overview",
            "taxonomy": {
                "context": context_taxonomy(has_working_state=bool(working_state)),
                "conceptual_priority": ["now", "long_term", "mid_term", "recent", "learning"],
                "injection_order": ["date", "user_notes", "profile", "belief_models", "working_state", "references", "recalled_memories"],
            },
            "profile": {
                "total": len(profile_facts),
                "top_categories": [
                    {"category": k, "count": v}
                    for k, v in sorted(profile_by_category.items(), key=lambda kv: kv[1], reverse=True)[:5]
                ],
                "high_confidence_facts": [
                    self._profile_fact_payload(os_svc, f)
                    for f in high_confidence
                ],
            },
            "beliefs": {
                "total": len(beliefs),
                "dirty_count": sum(1 for b in beliefs if int(b.get("dirty") or 0)),
                "top_domains": [
                    self._belief_payload(os_svc, {
                        "domain": b.get("domain", ""),
                        "summary": b.get("summary") or b.get("latest") or "",
                        "trajectory": b.get("trajectory") or "",
                        "signals": b.get("signals") or [],
                        "entries": b.get("entries") or [],
                        "dirty": int(b.get("dirty") or 0),
                        "updated": int(b.get("updated") or 0),
                    })
                    for b in beliefs[:5]
                ],
            },
            "reflections": {
                "total_recent": len(reflections),
                "success_count": sum(1 for r in reflections if bool(r.get("success"))),
                "failure_count": sum(1 for r in reflections if not bool(r.get("success"))),
                "top_task_types": [
                    {"task_type": k, "count": v}
                    for k, v in sorted(task_counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
                ],
                "latest_lessons": [
                    self._reflection_payload(os_svc, {
                        "lesson": r.get("lesson") or r.get("outcome") or "",
                        "strategy_hint": r.get("strategy_hint") or "",
                        "success": bool(r.get("success")),
                        "task_type": self._format_task_fingerprint(r.get("task_fingerprint", "")),
                        "session_id": r.get("session_id") or "",
                        "created": int(r.get("created") or 0),
                    })
                    for r in reflections[:5]
                ],
            },
            "memories": {
                "recent_items": recent_notes,
            },
        },)

    # ── __init__ ───────────────────────────────────────────────────────────────

    def __init__(self, gateway, config: ServerConfig, *, os_api: AgentOSService | None = None) -> None:
        self._gateway = gateway
        self._config = config
        self._os_api: AgentOSService = os_api or AgentOSService(gateway)
        # Session-local pending prompt-only skill command context.
        # Key: session_id, value: {"skill": str, "description": str}
        self._pending_skill_prompts: dict[str, dict[str, str]] = {}
        # Webhook handlers registered by connectors: path → async callable(path, query, body)
        self._webhook_handlers: dict[str, any] = {}
        # Track connected WS clients for broadcast (config_reloaded etc.)
        self._connected_clients: set = set()
        # Config file watcher state (populated by _start_config_watcher in HttpMixin)
        self._config_file_path = None
        self._config_file_mtime: float = 0.0
        self._config_watcher_task = None
        # Update subsystem
        self._update_service = UpdateService(
            cache_ttl_seconds=max(60, int(getattr(gateway.base_agent.config.update, "cache_ttl_seconds", 900))),
        )
        self._update_executor = UpdateExecutor()
        self._upgrade_lock = asyncio.Lock()
        self._upgrade_in_progress: bool = False
        self._upgrade_state: dict = {"in_progress": False}
        self._running_sessions: set[str] = set()
        # Server-level session registry: tasks survive individual WS connections
        self._session_tasks: dict[str, _SessionEntry] = {}

        # File upload directory (resolved from config or data_dir/uploads)
        from pathlib import Path
        upload_dir = config.upload_dir
        if upload_dir is None:
            upload_dir = gateway.base_agent.config.memory.data_dir / "uploads"
        self._upload_dir: Path = Path(upload_dir)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        from hushclaw.scheduler import Scheduler
        memory = gateway.memory
        self._scheduler = Scheduler(memory, gateway)
        # Inject scheduler into all agents so tools can reference it
        gateway.set_scheduler(self._scheduler)

        from hushclaw.connectors.manager import ConnectorsManager
        self._connectors = ConnectorsManager(
            gateway.base_agent.config.connectors,
            gateway,
            webhook_registry=self._webhook_handlers,
            calendar_config=gateway.base_agent.config.calendar,
            memory_store=gateway.memory,
        )
        # Cached result of playwright availability check (None = not yet checked).
        self._playwright_available: bool | None = None
        # Cached WebShellRegistry — distro doesn't change after startup.
        from hushclaw.web_shells import WebShellRegistry
        self._shell_registry = WebShellRegistry(self._os_api.distro)

    # ── Server start ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        try:
            from websockets.asyncio.server import serve as _ws_serve
        except ImportError:
            raise ImportError(
                "websockets>=12.0 is required for 'hushclaw serve'. "
                "Install with: pip install 'hushclaw[server]'"
            ) from None

        distro = getattr(self._os_api, "distro", None)
        if distro is not None:
            await distro.on_startup(self._os())

        api_port = self._config.port + 1
        log.info(
            "Starting HushClaw server on %s:%d  (HTTP API on port %d)",
            self._config.host, self._config.port, api_port,
        )

        async with _ws_serve(
            self._handle_client,
            self._config.host,
            self._config.port,
            # Allow base64-encoded files up to max_upload_mb through the WS channel.
            # Base64 overhead is ~4/3; add 10 % headroom and floor at 4 MB.
            max_size=max(4 * 1024 * 1024,
                         int(self._config.max_upload_mb * 1024 * 1024 * 1.5)),
            process_request=self._http_handler,
            # Increase ping timeout so long LLM calls (>40s) don't drop the connection.
            # ping_interval=30s means a ping is sent every 30s; ping_timeout=120s gives
            # the client 120s to respond before the server closes the connection.
            ping_interval=30,
            ping_timeout=120,
        ):
            # Start the companion HTTP API server (POST proxy for community/auth APIs).
            # websockets 16 only accepts GET connections (WebSocket upgrades), so we
            # run a minimal asyncio stream server on port+1 for POST endpoints.
            api_server = await asyncio.start_server(
                self._http_api_handler,
                self._config.host,
                api_port,
            )
            print(
                f"HushClaw server listening on "
                f"http://{self._config.host}:{self._config.port}"
            )
            print(
                f"HushClaw HTTP API listening on "
                f"http://{self._config.host}:{api_port}"
            )
            if self._config.api_key:
                print("API key authentication enabled (X-API-Key header).")
            # Defer non-critical startup work so the WebSocket is ready to
            # accept the first browser connection without waiting for connectors
            # (which may call ensure_package / do initial network I/O) and other
            # background services.  A 2-second delay gives the HTTP + WS servers
            # time to accept the first connection before any blocking work runs.
            asyncio.create_task(self._background_startup(), name="hc-bg-startup")
            try:
                async with api_server:
                    await asyncio.Future()  # run forever
            finally:
                if self._config_watcher_task:
                    self._config_watcher_task.cancel()
                await self._connectors.stop()
                await self._scheduler.stop()
                if distro is not None:
                    await distro.on_shutdown()

    # ── WebSocket client handler ───────────────────────────────────────────────

    async def _handle_client(self, ws) -> None:
        # Optional API key auth
        if self._config.api_key:
            key = _request_api_key(ws)
            if key != self._config.api_key:
                await ws.close(1008, "Unauthorized")
                return

        remote = getattr(ws, "remote_address", "?")
        log.info("Client connected: %s", remote)

        self._connected_clients.add(ws)
        owned_sids: set[str] = set()       # sessions this connection started or subscribed to

        # Immediately push config status so the UI can show the setup wizard if needed
        try:
            await ws.send(json.dumps(self._config_status()))
        except Exception:
            pass

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue

                msg_type = data.get("type", "chat")

                if msg_type == "stop":
                    sid = data.get("session_id", "")
                    entry = self._session_tasks.get(sid)
                    if entry and entry.task and not entry.task.done():
                        entry.task.cancel()
                    await self._emit_session_status(ws, sid, "idle", "stopped")
                    await ws.send(json.dumps({"type": "stopped", "session_id": sid}))

                elif msg_type == "subscribe":
                    sid = data.get("session_id", "")
                    await self._subscribe_session(ws, sid)
                    if sid:
                        owned_sids.add(sid)

                elif msg_type == "browser_handover_done":
                    sid = data.get("session_id", "")
                    event = self._gateway.handover_registry.get(sid)
                    if event:
                        event.set()

                elif msg_type in ("chat", "pipeline", "orchestrate"):
                    # Resolve session_id before task creation so stop can find it immediately.
                    agent = data.get("agent", "default")
                    sid = data.get("session_id") or make_id("s-")
                    if not data.get("session_id"):
                        data = dict(data)
                        data["session_id"] = sid

                    entry = self._get_or_create_session_entry(sid)
                    entry.subscriber = ws
                    sink = _SessionSink(entry)

                    task = asyncio.create_task(self._dispatch(sink, data))
                    entry.task = task
                    owned_sids.add(sid)

                    def _on_task_done(t, s=sid):
                        e = self._session_tasks.get(s)
                        if e:
                            e.finished_at = time.time()
                        try:
                            asyncio.get_event_loop().call_later(
                                _SESSION_TTL,
                                lambda: self._session_tasks.pop(s, None),
                            )
                        except Exception:
                            pass

                    task.add_done_callback(_on_task_done)

                else:
                    try:
                        await self._dispatch(ws, data)
                    except Exception as exc:
                        log.error("dispatch error for msg_type=%s: %s", data.get("type"), exc, exc_info=True)
                        try:
                            await ws.send(json.dumps({"type": "error", "message": str(exc)}))
                        except Exception:
                            pass

        except Exception as e:
            log.debug("Client %s disconnected: %s", remote, e)
        finally:
            self._connected_clients.discard(ws)
            # Tasks continue running after disconnect; just detach this WS as subscriber.
            for sid in owned_sids:
                e = self._session_tasks.get(sid)
                if e and e.subscriber is ws:
                    e.subscriber = None
            log.info("Client disconnected: %s", remote)

    # ── Central message router ─────────────────────────────────────────────────

    async def _dispatch(self, ws, data: dict, _session_ids=None) -> None:
        workspace = str(data.get("workspace") or "").strip()
        principal = RuntimePrincipal(
            principal_id="local-user",
            workspace_id=workspace,
            roles=("owner",),
            mode="personal",
            source_channel="webui",
        )
        with principal_context(principal):
            await self._dispatch_with_principal(ws, data, _session_ids)

    async def _dispatch_with_principal(self, ws, data: dict, _session_ids=None) -> None:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type == "chat":
            await self._handle_chat(ws, data)
        elif msg_type == "broadcast_mention":
            await self._handle_broadcast_mention(ws, data)
        elif msg_type == "pipeline":
            await self._handle_pipeline(ws, data)
        elif msg_type == "run_hierarchical":
            await self._handle_run_hierarchical(ws, data)
        elif msg_type == "orchestrate":
            await self._handle_orchestrate(ws, data)
        elif msg_type == "list_agents":
            await ws.send(json.dumps({"type": "agents", "items": self._gateway.list_agents()}))
        elif msg_type == "os_list_extensions":
            await ws.send(json.dumps({
                "type": "os_extensions",
                "items": self._os().list_extensions(),
            }))
        elif msg_type == "os_list_tools":
            await ws.send(json.dumps({
                "type": "os_tools",
                "items": self._os().list_tools(),
            }))
        elif msg_type == "os_get_runtime_profile":
            await ws.send(json.dumps({
                "type": "os_runtime_profile",
                **self._os().runtime_profile(),
            }))
        elif msg_type == "os_audit_events":
            await ws.send(json.dumps({
                "type": "os_audit_events",
                "items": self._os().audit_events(
                    session_id=str(data.get("session_id") or ""),
                    limit=int(data.get("limit") or 200),
                ),
            }))
        elif msg_type == "enterprise_get_overview":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_overview",
                **self._os().enterprise_overview(),
            }))
        elif msg_type == "enterprise_get_settings":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_settings",
                "settings": self._os().enterprise_settings(),
            }))
        elif msg_type == "enterprise_update_settings":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            settings = self._os().update_enterprise_settings(data.get("settings") or {})
            await ws.send(json.dumps({
                "type": "enterprise_settings",
                "settings": settings,
            }))
        elif msg_type == "enterprise_list_members":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_members",
                "items": self._os().list_members(),
            }))
        elif msg_type == "enterprise_upsert_member":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().upsert_member(data.get("member") or {})
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "member",
                "item": item,
                "members": self._os().list_members(),
            }))
        elif msg_type == "enterprise_deactivate_member":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().deactivate_member(str(data.get("member_id") or ""))
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "member",
                "item": item,
                "members": self._os().list_members(),
            }))
        elif msg_type == "enterprise_list_org_units":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_org_units",
                "items": self._os().list_org_units(),
            }))
        elif msg_type == "enterprise_upsert_org_unit":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().upsert_org_unit(data.get("unit") or {})
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "org_unit",
                "item": item,
                "org_units": self._os().list_org_units(),
            }))
        elif msg_type == "enterprise_list_positions":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_positions",
                "items": self._os().list_positions(),
            }))
        elif msg_type == "enterprise_upsert_position":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().upsert_position(data.get("position") or {})
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "position",
                "item": item,
                "positions": self._os().list_positions(),
            }))
        elif msg_type == "enterprise_list_roles":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_roles",
                "items": self._os().list_roles(),
                "assignments": self._os().list_role_assignments(),
            }))
        elif msg_type == "enterprise_upsert_role":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().upsert_role(data.get("role") or {})
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "role",
                "item": item,
                "roles": self._os().list_roles(),
                "assignments": self._os().list_role_assignments(),
            }))
        elif msg_type == "enterprise_assign_role":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().assign_role(
                str(data.get("member_id") or ""),
                str(data.get("role_id") or ""),
                scope=str(data.get("scope") or "org"),
                scope_id=str(data.get("scope_id") or ""),
            )
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "role_assignment",
                "item": item,
                "roles": self._os().list_roles(),
                "assignments": self._os().list_role_assignments(),
            }))
        elif msg_type == "enterprise_revoke_role":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            item = self._os().revoke_role(
                str(data.get("member_id") or ""),
                str(data.get("role_id") or ""),
                scope=str(data.get("scope") or "org"),
                scope_id=str(data.get("scope_id") or ""),
            )
            await ws.send(json.dumps({
                "type": "enterprise_directory_result",
                "resource": "role_assignment",
                "item": item,
                "roles": self._os().list_roles(),
                "assignments": self._os().list_role_assignments(),
            }))
        elif msg_type == "enterprise_list_foundation":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "enterprise_foundation",
                "items": self._os().foundation_catalog(),
            }))
        elif msg_type == "os_list_domains":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "os_domains",
                "items": self._os().list_domains(),
            }))
        elif msg_type == "enterprise_get_domain_config":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            domain_id = str(data.get("domain_id") or "")
            await ws.send(json.dumps({
                "type": "enterprise_domain_config",
                **self._os().domain_config(domain_id),
            }))
        elif msg_type == "enterprise_get_domain_dependencies":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            domain_id = str(data.get("domain_id") or "")
            await ws.send(json.dumps({
                "type": "enterprise_domain_dependencies",
                **self._os().domain_dependency_status(domain_id),
            }))
        elif msg_type == "enterprise_update_domain_config":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            domain_id = str(data.get("domain_id") or "")
            result = self._os().update_domain_config(domain_id, data.get("config") or {})
            await ws.send(json.dumps({
                "type": "enterprise_domain_config",
                **result,
            }))
        elif msg_type == "crm_list_records":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "crm_records",
                "entity_type": str(data.get("entity_type") or ""),
                "items": self._os().crm_records(
                    str(data.get("entity_type") or "lead"),
                    limit=int(data.get("limit") or 50),
                ),
            }))
        elif msg_type == "crm_list_events":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "crm_events",
                "items": self._os().crm_events(
                    entity_type=str(data.get("entity_type") or ""),
                    entity_id=str(data.get("entity_id") or ""),
                    limit=int(data.get("limit") or 50),
                ),
            }))
        elif msg_type == "crm_list_next_actions":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            await ws.send(json.dumps({
                "type": "crm_next_actions",
                "items": self._os().crm_next_actions(limit=int(data.get("limit") or 20)),
            }))
        elif msg_type == "crm_create_lead":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            domain = self._os().domain_registry().get("crm")
            lead = domain.store.upsert("lead", data.get("lead") or {}, actor_id=self._os().principal.principal_id)
            await ws.send(json.dumps({
                "type": "crm_mutation_result",
                "entity_type": "lead",
                "item": lead,
                "items": self._os().crm_records("lead", limit=50),
                "events": self._os().crm_events(limit=50),
                "next_actions": self._os().crm_next_actions(limit=20),
            }))
        elif msg_type == "crm_create_record":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            entity_type = str(data.get("entity_type") or "")
            result = self._os().crm_create_record(entity_type, data.get("record") or {})
            await ws.send(json.dumps({
                "type": "crm_mutation_result",
                "entity_type": entity_type,
                "result": result,
                "item": result.get("item"),
                "items": self._os().crm_records(entity_type, limit=50),
                "events": self._os().crm_events(limit=50),
                "next_actions": self._os().crm_next_actions(limit=20),
            }))
        elif msg_type == "crm_update_outbound_draft":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            result = self._os().crm_update_outbound_draft_status(
                str(data.get("draft_id") or ""),
                str(data.get("status") or ""),
            )
            await ws.send(json.dumps({
                "type": "crm_outbound_draft_result",
                "result": result,
                "items": self._os().crm_records("outbound_draft", limit=50),
                "events": self._os().crm_events(limit=50),
            }))
        elif msg_type == "crm_update_next_action":
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            result = self._os().crm_update_next_action_status(
                str(data.get("state_id") or ""),
                str(data.get("status") or ""),
            )
            await ws.send(json.dumps({
                "type": "crm_next_action_result",
                "result": result,
                "next_actions": self._os().crm_next_actions(limit=20),
                "events": self._os().crm_events(limit=50),
            }))
        elif msg_type in ("os_install_domain", "os_enable_domain", "os_disable_domain"):
            if not self._os().is_enterprise():
                await self._send_enterprise_required(ws, msg_type)
                return
            domain_id = str(data.get("domain_id") or "")
            scope = str(data.get("scope") or "org")
            if msg_type == "os_install_domain":
                result = self._os().install_domain(domain_id, scope=scope)
            elif msg_type == "os_enable_domain":
                result = self._os().enable_domain(domain_id, scope=scope)
            else:
                result = self._os().disable_domain(domain_id, scope=scope)
            await ws.send(json.dumps({
                "type": "os_domain_lifecycle_result",
                "action": msg_type.removeprefix("os_").removesuffix("_domain"),
                "result": result,
                "items": self._os().list_domains(),
            }))
        elif msg_type == "create_agent":
            name = data.get("name", "")
            try:
                self._gateway.create_agent(
                    name=name,
                    description=data.get("description", ""),
                    system_prompt=data.get("system_prompt", ""),
                    instructions=data.get("instructions", ""),
                    role=data.get("role", "specialist"),
                    team=data.get("team", ""),
                    reports_to=data.get("reports_to", ""),
                    capabilities=data.get("capabilities", []) or [],
                    tools=data.get("tools", []) or [],
                )
                await ws.send(json.dumps({
                    "type": "agent_created",
                    "name": name,
                    "agents": self._gateway.list_agents(),
                }))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "delete_agent":
            name = data.get("name", "")
            try:
                self._gateway.delete_agent(name)
                await ws.send(json.dumps({
                    "type": "agent_deleted",
                    "name": name,
                    "agents": self._gateway.list_agents(),
                }))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "get_agent":
            name = data.get("name", "")
            defn = self._gateway.get_agent_def(name)
            if defn is None:
                await ws.send(json.dumps({"type": "error", "message": f"Agent '{name}' not found."}))
            else:
                await ws.send(json.dumps({"type": "agent_detail", "agent": defn}))
        elif msg_type == "update_agent":
            name = data.get("name", "")
            try:
                self._gateway.update_agent(
                    name=name,
                    description=data.get("description"),
                    system_prompt=data.get("system_prompt"),
                    instructions=data.get("instructions"),
                    role=data.get("role"),
                    team=data.get("team"),
                    reports_to=data.get("reports_to"),
                    capabilities=data.get("capabilities"),
                    tools=data.get("tools"),
                )
                await ws.send(json.dumps({
                    "type": "agent_updated",
                    "name": name,
                    "agents": self._gateway.list_agents(),
                }))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "list_sessions":
            await self._handle_list_sessions(ws, data)
        elif msg_type == "list_memories":
            query = data.get("query", "")
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
            include_auto = bool(data.get("include_auto", False))
            include_kinds = self._normalize_memory_kind_filter(data.get("memory_kinds"))
            request_id = data.get("request_id")
            ws_name = (data.get("workspace") or "").strip()
            items, has_more = self._os().list_memories(
                query=query,
                limit=limit,
                offset=offset,
                include_auto=include_auto,
                memory_kinds=include_kinds,
                workspace=ws_name,
            )
            payload = {"type": "memories", "items": items, "offset": offset, "has_more": has_more}
            if request_id is not None:
                payload["request_id"] = request_id
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "get_memory_overview":
            await self._handle_get_memory_overview(ws, data)
        elif msg_type == "get_workspace_briefing":
            await self._handle_get_workspace_briefing(ws, data)
        elif msg_type == "accept_briefing_suggestion":
            await self._handle_accept_briefing_suggestion(ws, data)
        elif msg_type == "dismiss_briefing_suggestion":
            await ws.send(json.dumps({
                "type": "briefing_suggestion_dismissed",
                "suggestion_id": data.get("suggestion_id", ""),
                "ok": True,
            }))
        elif msg_type == "delete_memory":
            raw = data.get("note_id")
            note_id = str(raw).strip() if raw is not None else ""
            try:
                ok = self._os().delete_memory(note_id)
            except Exception as exc:
                log.error("forget(%s) failed: %s", note_id, exc, exc_info=True)
                ok = False
            # Send confirmation immediately
            await ws.send(json.dumps({"type": "memory_deleted", "note_id": note_id, "ok": ok}))
            # Server does NOT push a fresh list after delete — the client calls onMemoryDeleted
            # which triggers sendListMemories to re-fetch with correct filters/offset.
        elif msg_type == "compact_memories":
            try:
                stats = await self._compact_auto_memories()
                await ws.send(json.dumps({
                    "type": "memories_compacted",
                    "ok": True,
                    **stats,
                }))
            except Exception as e:
                log.error("compact_memories error: %s", e, exc_info=True)
                await ws.send(json.dumps({
                    "type": "memories_compacted",
                    "ok": False,
                    "error": str(e),
                }))
        elif msg_type == "list_belief_models":
            scopes = data.get("scopes") or None
            try:
                os_svc = self._os()
                items = [self._belief_payload(os_svc, item) for item in os_svc.list_belief_models(scopes=scopes)]
            except Exception as exc:
                log.error("list_belief_models failed: %s", exc, exc_info=True)
                items = []
            await ws.send(json.dumps({"type": "belief_models", "items": items}, default=str))
        elif msg_type == "rebuild_belief_models":
            scopes = data.get("scopes") or None
            dry_run = bool(data.get("dry_run"))
            try:
                stats = self._os().rebuild_belief_models(dry_run=dry_run, scopes=scopes)
                await ws.send(json.dumps({
                    "type": "belief_models_rebuilt",
                    "ok": True,
                    **stats,
                }, default=str))
            except Exception as exc:
                log.error("rebuild_belief_models failed: %s", exc, exc_info=True)
                await ws.send(json.dumps({
                    "type": "belief_models_rebuilt",
                    "ok": False,
                    "error": str(exc),
                }, default=str))
        elif msg_type == "list_profile_facts":
            try:
                os_svc = self._os()
                items = [self._profile_fact_payload(os_svc, item) for item in os_svc.list_profile_facts(limit=200)]
            except Exception as exc:
                log.error("list_profile_facts failed: %s", exc, exc_info=True)
                items = []
            await ws.send(json.dumps({"type": "profile_facts", "items": items}, default=str))
        elif msg_type == "delete_profile_fact":
            fact_id = str(data.get("fact_id") or "").strip()
            try:
                ok = self._os().delete_profile_fact(fact_id)
            except Exception as exc:
                log.error("delete_profile_fact(%s) failed: %s", fact_id, exc, exc_info=True)
                ok = False
            await ws.send(json.dumps({"type": "profile_fact_deleted", "fact_id": fact_id, "ok": ok}))
        elif msg_type == "delete_session":
            sid = data.get("session_id", "")
            ok = self._os().delete_session(sid)
            await ws.send(json.dumps({"type": "session_deleted", "session_id": sid, "ok": ok}))
        elif msg_type == "set_message_state":
            sid = (data.get("session_id") or "").strip()
            message_id = (data.get("message_id") or "").strip()
            action = (data.get("action") or "").strip()
            result = self._os().set_message_state(message_id, session_id=sid, action=action)
            if not result.get("ok") and result.get("error"):
                await ws.send(json.dumps({
                    "type": "message_state_updated",
                    "message_id": message_id,
                    "ok": False,
                    "error": result.get("error"),
                }))
                return
            await ws.send(json.dumps({
                "type": "message_state_updated",
                "message_id": result.get("message_id", ""),
                "session_id": sid,
                "action": action,
                "ok": bool(result.get("ok")),
            }))
        elif msg_type == "move_session_workspace":
            sid = data.get("session_id", "")
            workspace = (data.get("workspace") or "").strip()
            try:
                await self._gateway.move_session_workspace(sid, workspace)
                await ws.send(json.dumps({"type": "session_workspace_moved", "session_id": sid, "workspace": workspace, "ok": True}))
            except Exception as exc:
                await ws.send(json.dumps({"type": "session_workspace_moved", "session_id": sid, "workspace": workspace, "ok": False, "error": str(exc)}))
        elif msg_type == "get_session_history":
            await self._handle_get_session_history(ws, data)
        elif msg_type == "search_sessions":
            await self._handle_search_sessions(ws, data)
        elif msg_type == "get_session_lineage":
            await self._handle_get_session_lineage(ws, data)
        elif msg_type == "get_learning_state":
            await self._handle_get_learning_state(ws, data)
        elif msg_type == "list_scheduled_tasks":
            tasks = self._os().list_scheduled_tasks()
            await ws.send(json.dumps({"type": "scheduled_tasks", "tasks": tasks}, default=str))
        elif msg_type == "create_scheduled_task":
            task = self._os().create_scheduled_task(data)
            await ws.send(json.dumps({"type": "task_created", "task": task}, default=str))
        elif msg_type == "toggle_scheduled_task":
            task_id = data.get("task_id", "")
            enabled = bool(data.get("enabled", True))
            ok = self._os().toggle_scheduled_task(task_id, enabled)
            await ws.send(json.dumps({"type": "task_toggled", "task_id": task_id, "enabled": enabled, "ok": ok}))
        elif msg_type == "run_scheduled_task_now":
            task_id = data.get("task_id", "")
            tasks = self._os().list_scheduled_tasks()
            job = next((t for t in tasks if t["id"] == task_id), None)
            if job:
                asyncio.create_task(self._scheduler._run_job(job))
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": True}))
            else:
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": False}))
        elif msg_type == "delete_scheduled_task":
            task_id = data.get("task_id", "")
            ok = self._os().delete_scheduled_task(task_id)
            await ws.send(json.dumps({"type": "task_cancelled", "task_id": task_id, "ok": ok}))
        elif msg_type == "list_todos":
            status = data.get("status") or None
            items = self._os().list_todos(status=status)
            await ws.send(json.dumps({"type": "todos", "items": items}, default=str))
        elif msg_type == "create_todo":
            item = self._os().create_todo(data)
            await ws.send(json.dumps({"type": "todo_created", "item": item}, default=str))
        elif msg_type == "update_todo":
            todo_id = data.get("todo_id", "")
            fields = {k: v for k, v in data.items() if k not in ("type", "todo_id")}
            item = self._os().update_todo(todo_id, fields)
            if item:
                await ws.send(json.dumps({"type": "todo_updated", "item": item}, default=str))
            else:
                await ws.send(json.dumps({"type": "error", "message": f"Todo not found: {todo_id}"}))
        elif msg_type == "delete_todo":
            todo_id = data.get("todo_id", "")
            ok = self._os().delete_todo(todo_id)
            await ws.send(json.dumps({"type": "todo_deleted", "todo_id": todo_id, "ok": ok}))
        elif msg_type == "list_calendar_events":
            await self._handle_list_calendar_events(ws, data)
        elif msg_type == "create_calendar_event":
            await self._handle_create_calendar_event(ws, data)
        elif msg_type == "update_calendar_event":
            await self._handle_update_calendar_event(ws, data)
        elif msg_type == "delete_calendar_event":
            await self._handle_delete_calendar_event(ws, data)
        elif msg_type == "force_sync_caldav":
            await self._handle_force_sync_caldav(ws, data)
        elif msg_type == "full_resync_caldav":
            await self._handle_full_resync_caldav(ws, data)
        elif msg_type == "get_config_status":
            await ws.send(json.dumps(self._config_status()))
        elif msg_type == "init_workspace":
            await self._handle_init_workspace(ws, data)
        elif msg_type == "save_config":
            log.info("ws: save_config received save_client_id=%r", data.get("save_client_id"))
            await self._handle_save_config(ws, data)
        elif msg_type == "save_update_policy":
            await self._handle_save_update_policy(ws, data)
        elif msg_type == "test_provider":
            await self._handle_test_provider(ws, data)
        elif msg_type == "test_email":
            await self._handle_test_email(ws, data)
        elif msg_type == "test_calendar":
            await self._handle_test_calendar(ws, data)
        elif msg_type == "test_app_connector":
            await self._handle_test_app_connector(ws, data)
        elif msg_type == "list_models":
            await self._handle_list_models(ws, data)
        elif msg_type == "check_update":
            await self._handle_check_update(ws, data)
        elif msg_type == "run_update":
            await self._handle_run_update(ws, data)
        elif msg_type == "file_upload":
            await self._ws_handle_upload(ws, data)
        elif msg_type == "list_files":
            await self._handle_list_files(ws, data)
        elif msg_type == "ingest_file":
            await self._handle_ingest_file(ws, data)
        elif msg_type == "delete_file":
            await self._handle_delete_file(ws, data)
        elif msg_type == "list_skills":
            await self._handle_list_skills(ws, data)
        elif msg_type == "save_skill":
            await self._handle_save_skill(ws, data)
        elif msg_type == "get_skill_detail":
            await self._handle_get_skill_detail(ws, data)
        elif msg_type == "check_skills_health":
            await self._handle_check_skills_health(ws)
        elif msg_type == "set_skill_enabled":
            await self._handle_set_skill_enabled(ws, data)
        elif msg_type == "install_skill_repo":
            await self._handle_install_skill_repo(ws, data)
        elif msg_type == "install_skill_zip":
            await self._handle_install_skill_zip(ws, data)
        elif msg_type == "export_skills":
            await self._handle_export_skills(ws, data)
        elif msg_type == "import_skill_zip":
            await self._handle_import_skill_zip_upload(ws, data)
        elif msg_type == "delete_skill":
            await self._handle_delete_skill(ws, data)
        elif msg_type == "transsion_send_code":
            await self._handle_transsion_send_code(ws, data)
        elif msg_type == "transsion_login":
            await self._handle_transsion_login(ws, data)
        elif msg_type == "transsion_quota":
            await self._handle_transsion_quota(ws, data)
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))
