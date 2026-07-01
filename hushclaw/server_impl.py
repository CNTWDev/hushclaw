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
import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from hushclaw.config.schema import ServerConfig
from hushclaw.memory.kinds import ALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
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

    async def _broadcast_json(self, payload: dict) -> None:
        dead = []
        for client in list(getattr(self, "_connected_clients", set())):
            try:
                await self._send_json(client, payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self._connected_clients.discard(client)

    async def _broadcast_session_title_update(self, payload: dict) -> None:
        sid = str(payload.get("session_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not sid or not title:
            return
        await self._broadcast_json({
            "type": "session_renamed",
            "session_id": sid,
            "ok": True,
            "title": title,
            "title_source": str(payload.get("title_source") or ""),
        })

    async def _run_work_task_and_notify(self, task_id: str, *, agent: str = "default") -> None:
        async def _started(payload: dict) -> None:
            await self._broadcast_json({"type": "work_task_started", **payload})

        result = await self._scheduler.run_work_task_now(
            task_id,
            agent=agent or "default",
            worker_id="webui",
            on_started=_started,
        )
        await self._broadcast_json({"type": "work_task_run_result", **result})

    @staticmethod
    def _format_runtime_feed_event(event_type: str, payload: dict, ts: int) -> dict | None:
        et = str(event_type or "").removeprefix("ws:")
        data = dict(payload or {})
        if et == "tool_call":
            return {"level": "tool", "label": str(data.get("tool") or "tool"), "summary": f"Running {data.get('tool') or 'tool'}", "ts": ts}
        if et == "tool_result":
            return {
                "level": "error" if data.get("is_error") else "done",
                "label": str(data.get("tool") or "tool"),
                "summary": "Failed" if data.get("is_error") else "Completed",
                "ts": ts,
            }
        if et == "round_info":
            summary = f"{data.get('round') or 0}/{data.get('max_rounds') or 0}" if data.get("max_rounds") else str(data.get("round") or 0)
            return {"level": "thinking", "label": "Round", "summary": summary, "ts": ts}
        if et == "awaiting_user":
            return {"level": "wait", "label": "Waiting", "summary": "Waiting for your confirmation", "ts": ts}
        if et == "compaction":
            if data.get("effective") is False:
                return None
            archived = int(data.get("archived_messages", data.get("archived", 0)) or 0)
            kept = int(data.get("kept_messages", data.get("kept", 0)) or 0)
            return {
                "level": "info",
                "label": "Context compacted",
                "summary": f"Archived {archived}, kept {kept}",
                "ts": ts,
            }
        if et == "pipeline_step":
            return {"level": "pipeline", "label": str(data.get("agent") or "pipeline"), "summary": str(data.get("output") or "Pipeline step"), "ts": ts}
        if et == "user_amendment_queued":
            queue_size = int(data.get("queue_size") or 1)
            return {"level": "queued", "label": "Queued update", "summary": f"{queue_size} pending" if queue_size > 1 else "1 pending", "ts": ts}
        if et == "user_amendment_applied":
            safe_point = str(data.get("safe_point") or "").strip()
            return {"level": "amendment", "label": "Applying update", "summary": f"Replanning ({safe_point})" if safe_point else "Replanning", "ts": ts}
        if et == "run_state_changed":
            state = str(data.get("state") or "").strip()
            return {"level": "queued" if state == "superseded" else "info", "label": state or "run", "summary": str(data.get("reason") or ""), "ts": ts}
        if et == "thread_state_changed":
            return {"level": "thread", "label": "Thread", "summary": str(data.get("state") or "active"), "ts": ts}
        if et == "step_state_changed":
            return {
                "level": str(data.get("step_type") or "step"),
                "label": str(data.get("step_type") or "step"),
                "summary": str(data.get("summary") or data.get("state") or ""),
                "ts": ts,
            }
        if et == "research_job_started":
            max_urls = int(data.get("max_urls") or 0)
            mode = str(data.get("read_mode") or "mixed")
            summary = f"Planning up to {max_urls} sources · {mode}" if max_urls else f"Planning · {mode}"
            return {"level": "research", "label": "Research", "summary": summary, "ts": ts}
        if et == "research_queries_planned":
            planned = int(data.get("planned_queries") or 0)
            max_urls = int(data.get("max_urls") or 0)
            summary = f"{planned} quer{'y' if planned == 1 else 'ies'} · up to {max_urls} URLs" if max_urls else f"{planned} quer{'y' if planned == 1 else 'ies'}"
            return {"level": "research", "label": "Plan ready", "summary": summary, "ts": ts}
        if et == "research_search_progress":
            completed = int(data.get("completed") or 0)
            total = int(data.get("total") or 0)
            results = int(data.get("results") or 0)
            summary = f"{completed}/{total} queries · {results} results"
            return {"level": "research", "label": "Searching", "summary": summary, "ts": ts}
        if et == "research_read_progress":
            completed = int(data.get("completed") or 0)
            total = int(data.get("total") or 0)
            ok = int(data.get("ok") or 0)
            summary = f"{completed}/{total} URLs · {ok} readable"
            return {"level": "research", "label": "Reading", "summary": summary, "ts": ts}
        if et == "research_job_completed":
            telemetry = data.get("telemetry") or {}
            urls_selected = int(telemetry.get("urls_selected") or 0)
            summary = f"Completed with {urls_selected} source{'s' if urls_selected != 1 else ''}" if urls_selected else str(data.get("summary") or "Completed")
            return {"level": "done", "label": "Research", "summary": summary, "ts": ts}
        if et == "research_job_failed":
            return {"level": "error", "label": "Research", "summary": str(data.get("error") or "Failed"), "ts": ts}
        if et == "child_run_state_changed":
            state = str(data.get("state") or "").strip()
            return {
                "level": "error" if state == "failed" else ("done" if state == "completed" else ("wait" if state == "paused" else "child")),
                "label": str(data.get("agent") or data.get("run_kind") or "child"),
                "summary": str(data.get("summary") or state or ""),
                "ts": ts,
                "scope": "child",
                "state": state,
                "child_run_id": str(data.get("run_id") or ""),
                "run_id": str(data.get("parent_run_id") or ""),
            }
        return None

    def _session_runtime_feed_snapshot(self, session_id: str, *, limit: int = 20) -> list[dict]:
        mem = getattr(self._gateway, "memory", None)
        if mem is None:
            return []
        rows = mem.conn.execute(
            "SELECT type, payload_json, ts FROM events WHERE session_id=? AND type LIKE 'ws:%' ORDER BY ts DESC LIMIT ?",
            (session_id, max(1, int(limit))),
        ).fetchall()
        items: list[dict] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            event = self._format_runtime_feed_event(str(row["type"] or ""), payload, int(row["ts"] or 0))
            if event:
                items.append(event)
        return items

    def _session_runtime_snapshot(self, session_id: str, *, include_feed: bool = False) -> dict:
        registry = getattr(self, "_session_runtime", {})
        runtime = dict(registry.get(session_id) or {})
        if not runtime:
            runtime = {
                "session_id": session_id,
                "status": "idle",
                "phase": "idle",
                "summary": "Idle",
                "agent": "",
                "started_at": None,
                "updated_at": int(time.time() * 1000),
                "last_error": "",
                "requires_user": False,
            }
        entry = getattr(self, "_session_tasks", {}).get(session_id)
        if entry is not None and hasattr(entry, "runtime_meta"):
            meta = entry.runtime_meta() or {}
            if meta:
                runtime["thread_id"] = meta.get("thread_id") or runtime.get("thread_id", "")
                runtime["thread_state"] = meta.get("thread_state") or runtime.get("thread_state", "")
                runtime["thread_agent"] = meta.get("thread_agent") or runtime.get("thread_agent", "")
                runtime["run_id"] = meta.get("run_id") or runtime.get("run_id", "")
                runtime["run_seq"] = meta.get("run_seq") or runtime.get("run_seq", 0)
                runtime["run_state"] = meta.get("run_state") or runtime.get("run_state", "")
                runtime["trigger_type"] = meta.get("trigger_type") or runtime.get("trigger_type", "user")
                runtime["pending_amendments"] = meta.get("pending_amendments", runtime.get("pending_amendments", 0))
                runtime["last_completed_run_id"] = meta.get("last_completed_run_id") or runtime.get("last_completed_run_id", "")
                runtime["last_superseded_run_id"] = meta.get("last_superseded_run_id") or runtime.get("last_superseded_run_id", "")
                runtime["last_amendment_id"] = meta.get("last_amendment_id") or runtime.get("last_amendment_id", "")
                runtime["active_step"] = meta.get("active_step") or runtime.get("active_step", {})
                runtime["child_runs"] = list(meta.get("child_runs") or [])
        if entry is not None and callable(getattr(entry, "is_running", None)) and entry.is_running():
            runtime["status"] = runtime.get("status") if runtime.get("status") != "idle" else "running"
            if runtime["status"] == "running":
                runtime["phase"] = runtime.get("phase") or "thinking"
                runtime["summary"] = runtime.get("summary") or "Running"
        if include_feed:
            runtime["recent_events"] = self._session_runtime_feed_snapshot(session_id)
        return runtime

    def _attach_session_runtime(self, items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in items:
            row = dict(item)
            sid = str(row.get("session_id") or "")
            if sid:
                row["runtime"] = self._session_runtime_snapshot(sid)
            out.append(row)
        return out

    # ── Session query handlers ─────────────────────────────────────────────────

    async def _handle_list_sessions(self, ws, data: dict) -> None:
        gw_cfg = self._gateway.base_agent.config.gateway
        limit = int(data.get("limit", gw_cfg.session_list_limit))
        offset = max(0, int(data.get("offset", 0)))
        cursor = self._clean_optional_text(data.get("cursor"))
        include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
        max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
        ws_raw = data.get("workspace")
        workspace_filter = None if ws_raw is None else str(ws_raw).strip()
        items, has_more = self._os().list_sessions(
            limit=limit,
            offset=offset,
            cursor=cursor,
            include_scheduled=bool(include_scheduled),
            max_idle_days=max(0, max_idle_days),
            workspace=workspace_filter,
        )
        next_cursor = None
        if has_more and items:
            tail = items[-1]
            next_cursor = self._gateway.memory._encode_session_cursor(
                int(tail.get("last_turn") or 0),
                str(tail.get("session_id") or ""),
            )
        items = self._attach_session_runtime(items)
        await self._send_json(ws, {
            "type": "sessions",
            "items": items,
            "offset": offset,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "append": bool(cursor) or offset > 0,
            "has_more": has_more,
        })

    async def _handle_get_logs(self, ws, data: dict) -> None:
        from hushclaw.util.logging import recent_logs

        try:
            raw_limit = int(data.get("limit") or 300)
        except (TypeError, ValueError):
            raw_limit = 300
        limit = max(1, min(raw_limit, 1500))
        level = str(data.get("level") or "").strip().upper()
        if level and level not in logging._nameToLevel:
            level = ""
        query = str(data.get("query") or "").strip()
        try:
            logs = recent_logs(limit=limit, level=level, query=query)
            await self._send_json(ws, {"type": "logs", "ok": True, "items": logs})
        except Exception as exc:
            log.error("get_logs failed: %s", exc, exc_info=True)
            await self._send_json(ws, {"type": "logs", "ok": False, "items": [], "error": str(exc)})

    async def _handle_get_session_history(self, ws, data: dict) -> None:
        sid = data.get("session_id", "")
        history = self._os().session_history(sid)
        await self._send_json(ws, {
            "type": "session_history",
            "session_id": sid,
            "turns": history["turns"],
            "summary": history["summary"],
            "lineage": history["lineage"],
            "runtime": self._session_runtime_snapshot(sid, include_feed=True),
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
        items = self._attach_session_runtime(items)
        await self._send_json(ws, {
            "type": "session_search_results",
            "query": query,
            "items": items,
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
                os_svc._reflection_payload(os_svc, item)
                for item in state["reflections"]
            ],
            "skill_outcomes": state["skill_outcomes"],
        })

    async def _handle_get_memory_overview(self, ws, data: dict) -> None:
        payload = self._os().build_memory_overview_payload(
            session_id=data.get("session_id", "") or "",
            reflection_limit=int(data.get("reflection_limit", 30) or 30),
        )
        await self._send_json(ws, {"type": "memory_overview", **payload})

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
        self._session_runtime: dict[str, dict] = {}
        # Server-level session registry: tasks survive individual WS connections
        self._session_tasks: dict[str, _SessionEntry] = {}
        self._session_tasks_lock = asyncio.Lock()
        self._ws_principals: dict[int, RuntimePrincipal] = {}

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
        from hushclaw.app_connectors.runtime import AppConnectorRuntimeManager
        self._app_connector_runtime = AppConnectorRuntimeManager(
            gateway.base_agent.config.app_connectors,
            gateway.memory,
            gateway=gateway,
        )
        # Cached result of playwright availability check (None = not yet checked).
        self._playwright_available: bool | None = None
        # Cached WebShellRegistry — distro doesn't change after startup.
        from hushclaw.web_shells import WebShellRegistry
        self._shell_registry = WebShellRegistry(self._os_api.distro)
        gateway.set_session_title_update_callback(self._broadcast_session_title_update)

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
                await self._app_connector_runtime.stop()
                await self._connectors.stop()
                await self._scheduler.stop()
                if distro is not None:
                    await distro.on_shutdown()

    # ── WebSocket client handler ───────────────────────────────────────────────

    async def _handle_client(self, ws) -> None:
        api_key_authenticated = False
        if self._config.api_key:
            key = _request_api_key(ws)
            if key == self._config.api_key:
                api_key_authenticated = True
            else:
                await ws.close(1008, "Unauthorized")
                return

        principal = self._principal_for_ws(ws, api_key_authenticated=api_key_authenticated)
        if principal is None:
            await ws.close(1008, "Enterprise login required")
            return

        remote = getattr(ws, "remote_address", "?")
        log.info("Client connected: %s principal=%s", remote, principal.principal_id)

        self._connected_clients.add(ws)
        self._ws_principals[id(ws)] = principal
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
                    entry = await self._get_session_entry(sid)
                    task = None
                    if entry is not None:
                        async with self._entry_lock(entry):
                            task = entry.task if entry.task and not entry.task.done() else None
                    if task is not None:
                        task.cancel()
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

                    existing = await self._get_session_entry(sid)
                    if msg_type == "chat" and existing is not None and existing.is_running():
                        normalized = self._normalize_chat_request(data)
                        if not normalized.get("text"):
                            await ws.send(json.dumps({"type": "error", "message": "Empty text", "session_id": sid}))
                            continue
                        async with self._entry_lock(existing):
                            existing.subscriber = ws
                            amendment = existing.queue_amendment(normalized)
                            queue_size = len(getattr(existing, "pending_amendments", []) or [])
                            target_run_id = str(getattr(existing, "active_run_id", "") or "")
                        await ws.send(json.dumps({
                            "type": "session",
                            "session_id": sid,
                        }))
                        await ws.send(json.dumps({
                            "type": "user_amendment_queued",
                            "session_id": sid,
                            "queue_size": queue_size,
                            "target_run_id": target_run_id,
                            "amendment_id": str(amendment.get("amendment_id") or ""),
                            "text": str(amendment.get("text") or "")[:400],
                            "agent": str(amendment.get("agent") or agent),
                            "client_turn_id": str(amendment.get("client_turn_id") or ""),
                        }))
                        if amendment.get("queue_limited"):
                            await ws.send(json.dumps({
                                "type": "user_amendment_queue_limited",
                                "session_id": sid,
                                "queue_size": queue_size,
                                "amendment_id": str(amendment.get("amendment_id") or ""),
                                "client_turn_id": str(amendment.get("client_turn_id") or ""),
                            }))
                        owned_sids.add(sid)
                        continue

                    entry, generation = await self._prepare_session_entry_for_request(sid)
                    async with self._entry_lock(entry):
                        entry.subscriber = ws
                    sink = _SessionSink(entry, generation=generation)

                    task = asyncio.create_task(self._dispatch(sink, data, principal=principal))
                    async with self._entry_lock(entry):
                        entry.task = task
                    owned_sids.add(sid)

                    def _on_task_done(t, s=sid):
                        async def _finalize_session_task():
                            e = await self._get_session_entry(s)
                            if e is not None:
                                async with self._entry_lock(e):
                                    e.finished_at = time.time()
                            await asyncio.sleep(_SESSION_TTL)
                            lock = self._ensure_session_tasks_lock()
                            async with lock:
                                current = self._session_tasks.get(s)
                                if current is e and current is not None and not current.is_running():
                                    self._session_tasks.pop(s, None)
                        try:
                            asyncio.create_task(_finalize_session_task(), name=f"session-task-finalize:{s[:12]}")
                        except Exception:
                            pass

                    task.add_done_callback(_on_task_done)

                else:
                    try:
                        await self._dispatch(ws, data, principal=principal)
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
            self._ws_principals.pop(id(ws), None)
            # Tasks continue running after disconnect; just detach this WS as subscriber.
            for sid in owned_sids:
                e = await self._get_session_entry(sid)
                if e:
                    async with self._entry_lock(e):
                        if e.subscriber is ws:
                            e.subscriber = None
            log.info("Client disconnected: %s", remote)

    # ── Central message router ─────────────────────────────────────────────────

    def _principal_for_ws(self, ws, *, api_key_authenticated: bool = False) -> RuntimePrincipal | None:
        workspace = ""
        if api_key_authenticated:
            return self._fallback_principal(workspace=workspace)
        return self._fallback_principal(workspace=workspace)

    def _fallback_principal(self, *, workspace: str = "") -> RuntimePrincipal:
        os_api = getattr(self, "_os_api", None)
        distro = getattr(os_api, "distro", None)
        if distro is not None:
            try:
                return distro.runtime_principal(
                    principal_id="local-user",
                    workspace_id=workspace,
                    roles=("owner",),
                    source_channel="webui",
                    auth_context={"auth": "api_key_or_local"},
                )
            except Exception:
                pass
        return RuntimePrincipal(
            principal_id="local-user",
            workspace_id=workspace,
            roles=("owner",),
            mode="personal",
            source_channel="webui",
            auth_context={"auth": "api_key_or_local"},
        )

    async def _dispatch(self, ws, data: dict, _session_ids=None, *, principal: RuntimePrincipal | None = None) -> None:
        workspace = str(data.get("workspace") or "").strip()
        resolved = principal or getattr(self, "_ws_principals", {}).get(id(ws)) or self._fallback_principal(workspace=workspace)
        if workspace and resolved.workspace_id != workspace:
            resolved = RuntimePrincipal(
                principal_id=resolved.principal_id,
                org_id=resolved.org_id,
                workspace_id=workspace,
                roles=resolved.roles,
                mode=resolved.mode,
                source_channel=resolved.source_channel,
                auth_context=resolved.auth_context,
            )
        with principal_context(resolved):
            await self._dispatch_with_principal(ws, data, _session_ids)

    async def _dispatch_with_principal(self, ws, data: dict, _session_ids=None) -> None:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type == "chat":
            await self._handle_chat(ws, data)
        elif msg_type == "test_agent":
            await self._handle_test_agent(ws, data)
        elif msg_type == "broadcast_mention":
            await self._handle_broadcast_mention(ws, data)
        elif msg_type == "pipeline":
            await self._handle_pipeline(ws, data)
        elif msg_type == "orchestrate":
            await self._handle_orchestrate(ws, data)
        elif msg_type == "list_agents":
            await ws.send(json.dumps({"type": "agents", "items": self._os().list_agents()}))
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
        elif msg_type == "opc_get_overview":
            opc = self._os().solutions["opc"]
            await ws.send(json.dumps({
                "type": "opc_overview",
                **opc.overview(),
            }, default=str))
        elif msg_type == "opc_list_teams":
            opc = self._os().solutions["opc"]
            await ws.send(json.dumps({
                "type": "opc_teams",
                "items": opc.list_teams(),
            }, default=str))
        elif msg_type == "opc_create_team":
            opc = self._os().solutions["opc"]
            try:
                item = opc.create_team(data.get("team") or data)
                await ws.send(json.dumps({
                    "type": "opc_team_saved",
                    "item": item,
                    "items": opc.list_teams(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_update_team":
            opc = self._os().solutions["opc"]
            try:
                item = opc.update_team(
                    str(data.get("team_id") or ""),
                    data.get("team") or data.get("fields") or {},
                )
                await ws.send(json.dumps({
                    "type": "opc_team_saved",
                    "item": item,
                    "items": opc.list_teams(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_archive_team":
            opc = self._os().solutions["opc"]
            try:
                item = opc.archive_team(str(data.get("team_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_team_saved",
                    "item": item,
                    "items": opc.list_teams(),
                    "channels": opc.list_channels(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_list_employee_drafts":
            opc = self._os().solutions["opc"]
            await ws.send(json.dumps({
                "type": "opc_employee_drafts",
                "items": opc.list_employee_drafts(),
                "skill_recommendations": opc.list_skill_recommendations(),
            }, default=str))
        elif msg_type == "opc_draft_employee":
            opc = self._os().solutions["opc"]
            try:
                item = await opc.draft_employee(
                    str(data.get("requirement") or ""),
                    team_id=str(data.get("team_id") or ""),
                )
                await ws.send(json.dumps({
                    "type": "opc_employee_draft",
                    "item": item,
                    "items": opc.list_employee_drafts(),
                    "skill_recommendations": opc.list_skill_recommendations(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_update_employee_draft":
            opc = self._os().solutions["opc"]
            try:
                item = opc.update_employee_draft(
                    str(data.get("draft_id") or ""),
                    data.get("fields") or {},
                )
                await ws.send(json.dumps({
                    "type": "opc_employee_draft",
                    "item": item,
                    "items": opc.list_employee_drafts(),
                    "skill_recommendations": opc.list_skill_recommendations(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_delete_employee_draft":
            opc = self._os().solutions["opc"]
            try:
                item = opc.delete_employee_draft(str(data.get("draft_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_employee_draft",
                    "item": item,
                    "items": opc.list_employee_drafts(),
                    "skill_recommendations": opc.list_skill_recommendations(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_create_employee_from_draft":
            opc = self._os().solutions["opc"]
            try:
                result = opc.create_employee_from_draft(str(data.get("draft_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_employee_created",
                    **result,
                    "employees": opc.list_employees(),
                    "teams": opc.list_teams(),
                    "channels": opc.list_channels(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_update_employee":
            opc = self._os().solutions["opc"]
            try:
                item = opc.update_employee(
                    str(data.get("employee_id") or ""),
                    data.get("employee") or data.get("fields") or {},
                )
                await ws.send(json.dumps({
                    "type": "opc_employee_saved",
                    "item": item,
                    "employees": opc.list_employees(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_archive_employee":
            opc = self._os().solutions["opc"]
            try:
                item = opc.archive_employee(str(data.get("employee_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_employee_saved",
                    "item": item,
                    "employees": opc.list_employees(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_approve_employee_skill":
            opc = self._os().solutions["opc"]
            try:
                item = opc.approve_employee_skill(
                    str(data.get("draft_id") or ""),
                    str(data.get("recommendation_id") or ""),
                )
                await ws.send(json.dumps({
                    "type": "opc_employee_skill_approved",
                    "item": item,
                    "skill_recommendations": opc.list_skill_recommendations(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_list_channels":
            opc = self._os().solutions["opc"]
            await ws.send(json.dumps({
                "type": "opc_channels",
                "items": opc.list_channels(),
            }, default=str))
        elif msg_type == "opc_create_channel":
            opc = self._os().solutions["opc"]
            try:
                item = opc.create_channel(data.get("channel") or data)
                await ws.send(json.dumps({
                    "type": "opc_channel_saved",
                    "item": item,
                    "items": opc.list_channels(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_get_channel_history":
            opc = self._os().solutions["opc"]
            try:
                channel_id = str(data.get("channel_id") or "")
                await ws.send(json.dumps({
                    "type": "opc_channel_history",
                    "channel_id": channel_id,
                    "items": opc.get_channel_history(
                        channel_id,
                        limit=int(data.get("limit") or 100),
                    ),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_send_channel_message":
            opc = self._os().solutions["opc"]
            try:
                result = await opc.send_channel_message(
                    str(data.get("channel_id") or ""),
                    str(data.get("text") or ""),
                    goal_id=str(data.get("goal_id") or ""),
                    target=str(data.get("target") or "mentioned"),
                    agent_names=data.get("agent_names") or [],
                )
                await ws.send(json.dumps({
                    "type": "opc_channel_message_result",
                    **result,
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_create_goal":
            opc = self._os().solutions["opc"]
            try:
                item = opc.create_goal(data.get("goal") or data)
                await ws.send(json.dumps({
                    "type": "opc_goal_saved",
                    "item": item,
                    "items": opc.list_goals(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_update_goal":
            opc = self._os().solutions["opc"]
            try:
                item = opc.update_goal(
                    str(data.get("goal_id") or ""),
                    data.get("goal") or data.get("fields") or {},
                )
                await ws.send(json.dumps({
                    "type": "opc_goal_saved",
                    "item": item,
                    "items": opc.list_goals(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_archive_goal":
            opc = self._os().solutions["opc"]
            try:
                item = opc.archive_goal(str(data.get("goal_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_goal_saved",
                    "item": item,
                    "items": opc.list_goals(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_complete_goal":
            opc = self._os().solutions["opc"]
            try:
                item = opc.complete_goal(str(data.get("goal_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_goal_saved",
                    "item": item,
                    "items": opc.list_goals(),
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_plan_goal":
            opc = self._os().solutions["opc"]
            try:
                result = await opc.plan_goal(
                    str(data.get("goal_id") or ""),
                    team_id=str(data.get("team_id") or ""),
                )
                await ws.send(json.dumps({
                    "type": "opc_goal_plan",
                    **result,
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_approve_goal_plan":
            opc = self._os().solutions["opc"]
            try:
                result = opc.approve_goal_plan(
                    str(data.get("goal_id") or ""),
                    data.get("work_item_ids") or None,
                )
                await ws.send(json.dumps({
                    "type": "opc_goal_approved",
                    **result,
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_start_discussion":
            opc = self._os().solutions["opc"]
            try:
                item = await opc.start_discussion(
                    team_id=str(data.get("team_id") or ""),
                    topic=str(data.get("topic") or ""),
                    goal_id=str(data.get("goal_id") or ""),
                )
                await ws.send(json.dumps({
                    "type": "opc_discussion",
                    "item": item,
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "opc_get_discussion":
            opc = self._os().solutions["opc"]
            try:
                item = opc.summarize_discussion(str(data.get("discussion_id") or ""))
                await ws.send(json.dumps({
                    "type": "opc_discussion_summary",
                    **item,
                }, default=str))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "create_agent":
            name = data.get("name", "")
            try:
                self._gateway.create_agent(
                    name=name,
                    description=data.get("description", ""),
                    system_prompt=data.get("system_prompt", ""),
                    instructions=data.get("instructions", ""),
                    routing_tags=data.get("routing_tags", []) or [],
                    tools=data.get("tools", []) or [],
                )
                await ws.send(json.dumps({
                    "type": "agent_created",
                    "ok": True,
                    "name": name,
                    "agent": self._gateway.get_agent_def(name),
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
                    routing_tags=data.get("routing_tags"),
                    tools=data.get("tools"),
                )
                await ws.send(json.dumps({
                    "type": "agent_updated",
                    "ok": True,
                    "name": name,
                    "agent": self._gateway.get_agent_def(name),
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
            payload = {
                "type": "memories",
                "items": items,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }
            if request_id is not None:
                payload["request_id"] = request_id
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "get_memory_overview":
            await self._handle_get_memory_overview(ws, data)
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
                items = [os_svc._belief_payload(os_svc, item) for item in os_svc.list_belief_models(scopes=scopes)]
                payload = {"type": "belief_models", "ok": True, "items": items}
            except Exception as exc:
                log.error("list_belief_models failed: %s", exc, exc_info=True)
                payload = {
                    "type": "belief_models",
                    "ok": False,
                    "items": [],
                    "error": str(exc),
                }
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "list_opinion_threads":
            try:
                os_svc = self._os()
                limit = max(1, min(int(data.get("limit", 50)), 200))
                offset = max(0, int(data.get("offset", 0)))
                items, total, has_more = os_svc.list_opinion_threads(
                    domain=str(data.get("domain") or "").strip(),
                    scope=str(data.get("scope") or "").strip(),
                    query=str(data.get("query") or "").strip(),
                    limit=limit,
                    offset=offset,
                )
                payload = {
                    "type": "opinion_threads",
                    "ok": True,
                    "items": [os_svc._opinion_thread_payload(os_svc, item) for item in items],
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": has_more,
                }
            except Exception as exc:
                log.error("list_opinion_threads failed: %s", exc, exc_info=True)
                payload = {
                    "type": "opinion_threads",
                    "ok": False,
                    "items": [],
                    "total": 0,
                    "offset": int(data.get("offset", 0) or 0),
                    "limit": int(data.get("limit", 50) or 50),
                    "has_more": False,
                    "error": str(exc),
                }
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "get_opinion_thread":
            thread_id = str(data.get("thread_id") or "").strip()
            event_limit = max(1, min(int(data.get("event_limit", 20)), 200))
            event_offset = max(0, int(data.get("event_offset", 0)))
            try:
                os_svc = self._os()
                thread = os_svc.get_opinion_thread(
                    thread_id=thread_id,
                    event_limit=event_limit,
                    event_offset=event_offset,
                )
                if thread is None:
                    payload = {
                        "type": "opinion_thread_detail",
                        "ok": False,
                        "thread_id": thread_id,
                        "error": "Opinion thread not found.",
                    }
                else:
                    payload = {
                        "type": "opinion_thread_detail",
                        "ok": True,
                        "thread_id": thread_id,
                        "item": os_svc._opinion_thread_payload(
                            os_svc,
                            thread,
                            event_limit=event_limit,
                            event_offset=event_offset,
                        ),
                    }
            except Exception as exc:
                log.error("get_opinion_thread failed: %s", exc, exc_info=True)
                payload = {
                    "type": "opinion_thread_detail",
                    "ok": False,
                    "thread_id": thread_id,
                    "error": str(exc),
                }
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "get_belief_model":
            domain = str(data.get("domain") or "").strip()
            scope = str(data.get("scope") or "global").strip() or "global"
            entry_limit = max(1, min(int(data.get("entry_limit", 10)), 100))
            entry_offset = max(0, int(data.get("entry_offset", 0)))
            try:
                os_svc = self._os()
                model = os_svc.get_belief_model(domain=domain, scope=scope)
                if model is None:
                    payload = {
                        "type": "belief_model_detail",
                        "ok": False,
                        "domain": domain,
                        "scope": scope,
                        "error": "Belief model not found.",
                    }
                else:
                    payload = {
                        "type": "belief_model_detail",
                        "ok": True,
                        "domain": domain,
                        "scope": scope,
                        "item": os_svc._belief_payload(
                            os_svc,
                            model,
                            entry_limit=entry_limit,
                            entry_offset=entry_offset,
                        ),
                    }
            except Exception as exc:
                log.error("get_belief_model failed: %s", exc, exc_info=True)
                payload = {
                    "type": "belief_model_detail",
                    "ok": False,
                    "domain": domain,
                    "scope": scope,
                    "error": str(exc),
                }
            await ws.send(json.dumps(payload, default=str))
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
                limit = max(1, min(int(data.get("limit", 50)), 200))
                offset = max(0, int(data.get("offset", 0)))
                query = str(data.get("query") or "").strip()
                raw_category = str(data.get("category") or "").strip()
                categories = [raw_category] if raw_category else None
                facts, total, has_more = os_svc.list_profile_facts(
                    limit=limit,
                    offset=offset,
                    query=query,
                    categories=categories,
                )
                items = [os_svc._profile_fact_payload(os_svc, item) for item in facts]
                payload = {
                    "type": "profile_facts",
                    "ok": True,
                    "items": items,
                    "offset": offset,
                    "limit": limit,
                    "total": total,
                    "has_more": has_more,
                    "query": query,
                    "category": raw_category,
                }
            except Exception as exc:
                log.error("list_profile_facts failed: %s", exc, exc_info=True)
                payload = {
                    "type": "profile_facts",
                    "ok": False,
                    "items": [],
                    "error": str(exc),
                }
            await ws.send(json.dumps(payload, default=str))
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
        elif msg_type == "rename_session":
            sid = str(data.get("session_id") or "").strip()
            title = str(data.get("title") or "")
            try:
                result = self._os().rename_session(sid, title)
            except Exception as exc:
                result = {"ok": False, "session_id": sid, "error": str(exc)}
            await ws.send(json.dumps({
                "type": "session_renamed",
                "session_id": sid,
                **result,
            }))
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
            limit = max(1, min(int(data.get("limit") or 30), 100))
            offset = max(0, int(data.get("offset") or 0))
            items, has_more = self._os().list_todos(status=status, limit=limit, offset=offset)
            await ws.send(json.dumps({
                "type": "todos",
                "items": items,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }, default=str))
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
        elif msg_type == "list_insights":
            limit = max(1, min(int(data.get("limit") or 30), 100))
            offset = max(0, int(data.get("offset") or 0))
            view = str(data.get("view") or "curated").strip().lower()
            if view not in {"curated", "suggested", "all"}:
                view = "curated"
            items, has_more = self._os().list_insights(limit=limit, offset=offset, view=view)
            await ws.send(json.dumps({
                "type": "insights",
                "view": view,
                "items": items,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }, default=str))
        elif msg_type == "create_insight":
            item = self._os().create_insight(data)
            if item:
                await ws.send(json.dumps({"type": "insight_created", "item": item}, default=str))
            else:
                await ws.send(json.dumps({"type": "error", "message": "Insight text cannot be empty"}))
        elif msg_type == "delete_insight":
            note_id = str(data.get("note_id") or "").strip()
            ok = self._os().delete_insight(note_id)
            await ws.send(json.dumps({"type": "insight_deleted", "note_id": note_id, "ok": ok}))
        elif msg_type == "preview_insight_cleanup":
            limit = max(1, min(int(data.get("limit") or 50), 100))
            payload = self._os().preview_insight_cleanup(limit=limit)
            await ws.send(json.dumps({
                "type": "insight_cleanup_preview",
                "ok": True,
                **payload,
            }, default=str))
        elif msg_type == "apply_insight_cleanup":
            stats = self._os().apply_insight_cleanup(data)
            await ws.send(json.dumps({
                "type": "insight_cleanup_applied",
                "ok": True,
                **stats,
            }, default=str))
        elif msg_type == "list_work_tasks":
            status = data.get("status") or None
            limit = int(data.get("limit") or 100)
            tasks = self._os().list_work_tasks(status=status, limit=limit)
            await ws.send(json.dumps({"type": "work_tasks", "tasks": tasks}, default=str))
        elif msg_type == "create_work_task":
            task = self._os().create_work_task(data)
            await ws.send(json.dumps({"type": "work_task_created", "task": task}, default=str))
        elif msg_type == "claim_work_task":
            task_id = data.get("task_id", "")
            run = self._os().claim_work_task(
                task_id,
                worker_id=data.get("worker_id", "webui"),
                session_id=data.get("session_id", ""),
            )
            await ws.send(json.dumps({"type": "work_task_claimed", "task_id": task_id, "run": run, "ok": bool(run)}, default=str))
        elif msg_type == "run_work_task_now":
            task_id = data.get("task_id", "")
            agent = data.get("agent", "") or "default"
            asyncio.create_task(self._run_work_task_and_notify(task_id, agent=agent))
            await ws.send(json.dumps({"type": "work_task_triggered", "task_id": task_id, "ok": True}, default=str))
        elif msg_type == "complete_work_task":
            run_id = data.get("run_id", "")
            ok = self._os().complete_work_task(run_id, result=data.get("result", ""))
            await ws.send(json.dumps({"type": "work_task_completed", "run_id": run_id, "ok": ok}, default=str))
        elif msg_type == "retry_work_task":
            task_id = data.get("task_id", "")
            task = self._os().retry_work_task(task_id)
            await ws.send(json.dumps({"type": "work_task_retried", "task_id": task_id, "task": task, "ok": bool(task)}, default=str))
        elif msg_type == "get_logs":
            await self._handle_get_logs(ws, data)
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
        elif msg_type == "prepare_update":
            await self._handle_prepare_update(ws, data)
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
        elif msg_type == "get_agent_runtime_status":
            await self._handle_get_agent_runtime_status(ws, data)
        elif msg_type == "check_skills_health":
            await self._handle_check_skills_health(ws)
        elif msg_type == "set_skill_enabled":
            await self._handle_set_skill_enabled(ws, data)
        elif msg_type == "install_skill_repo":
            await self._handle_install_skill_repo(ws, data)
        elif msg_type == "install_skill_zip":
            await self._handle_install_skill_zip(ws, data)
        elif msg_type == "inspect_skill_source":
            await self._handle_inspect_skill_source(ws, data)
        elif msg_type == "install_skill_source":
            await self._handle_install_skill_source(ws, data)
        elif msg_type == "export_skills":
            await self._handle_export_skills(ws, data)
        elif msg_type == "import_skill_zip":
            await self._handle_import_skill_zip_upload(ws, data)
        elif msg_type == "delete_skill":
            await self._handle_delete_skill(ws, data)
        elif msg_type == "prune_skill_overrides":
            await self._handle_prune_skill_overrides(ws, data)
        elif msg_type == "transsion_send_code":
            await self._handle_transsion_send_code(ws, data)
        elif msg_type == "transsion_login":
            await self._handle_transsion_login(ws, data)
        elif msg_type == "transsion_quota":
            await self._handle_transsion_quota(ws, data)
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))
