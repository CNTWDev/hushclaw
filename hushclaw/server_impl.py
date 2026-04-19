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
from urllib.parse import parse_qs, urlparse

from hushclaw.config.schema import ServerConfig
from hushclaw.memory.kinds import ALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
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

    # ── Session query handlers ─────────────────────────────────────────────────

    async def _handle_list_sessions(self, ws, data: dict) -> None:
        gw_cfg = self._gateway.base_agent.config.gateway
        limit = int(data.get("limit", gw_cfg.session_list_limit))
        offset = max(0, int(data.get("offset", 0)))
        include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
        max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
        workspace_filter = self._clean_optional_text(data.get("workspace"))
        fetch_limit = limit + 1  # fetch one extra to detect has_more
        items = self._gateway.memory.list_sessions(
            limit=max(1, fetch_limit),
            include_scheduled=bool(include_scheduled),
            max_idle_days=max(0, max_idle_days),
            workspace=workspace_filter,
            offset=offset,
        )
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]
        await self._send_json(ws, {
            "type": "sessions",
            "items": items,
            "offset": offset,
            "has_more": has_more,
        })

    async def _handle_get_session_history(self, ws, data: dict) -> None:
        sid = data.get("session_id", "")
        turns = self._gateway.memory.load_session_turns(sid)
        summary = self._gateway.memory.load_session_summary(sid) if sid else None
        lineage = self._gateway.memory.get_session_lineage(sid) if sid else []
        await self._send_json(ws, {
            "type": "session_history",
            "session_id": sid,
            "turns": turns,
            "summary": summary,
            "lineage": lineage,
        })

    async def _handle_search_sessions(self, ws, data: dict) -> None:
        query = data.get("query", "")
        limit = int(data.get("limit", 20))
        include_scheduled = bool(data.get("include_scheduled", True))
        workspace_filter = self._clean_optional_text(data.get("workspace"))
        items = self._gateway.memory.search_sessions(
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

    async def _handle_get_session_lineage(self, ws, data: dict) -> None:
        sid = data.get("session_id", "")
        items = self._gateway.memory.get_session_lineage(sid) if sid else []
        await self._send_json(ws, {
            "type": "session_lineage",
            "session_id": sid,
            "items": items,
        })

    async def _handle_get_learning_state(self, ws, data: dict) -> None:
        mem = self._gateway.memory
        await self._send_json(ws, {
            "type": "learning_state",
            "profile_snapshot": mem.user_profile.get_profile_snapshot(),
            "profile_text": mem.user_profile.render_profile_context(max_chars=1400),
            "reflections": mem.list_reflections(limit=int(data.get("reflection_limit", 8) or 8)),
            "skill_outcomes": mem.list_recent_skill_outcomes(limit=int(data.get("skill_outcome_limit", 10) or 10)),
        })

    # ── __init__ ───────────────────────────────────────────────────────────────

    def __init__(self, gateway, config: ServerConfig) -> None:
        self._gateway = gateway
        self._config = config
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
        )
        # Cached result of playwright availability check (None = not yet checked).
        self._playwright_available: bool | None = None

    # ── Server start ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        try:
            from websockets.asyncio.server import serve as _ws_serve
        except ImportError:
            raise ImportError(
                "websockets>=12.0 is required for 'hushclaw serve'. "
                "Install with: pip install 'hushclaw[server]'"
            ) from None

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
            agent = self._gateway.base_agent
            # Tags excluded at DB level — avoids Python post-filter skewing pagination
            exclude_tags = sorted(SYSTEM_MEMORY_TAGS)
            if not include_auto:
                exclude_tags.append("_auto_extract")
            fetch_limit = limit + 1  # fetch one extra to detect has_more
            if query:
                # Search path: no offset support; filter in Python
                items = agent.search(query, limit=fetch_limit, include_kinds=include_kinds)
                items = [m for m in items if not self._is_system_note(m)]
                if not include_auto:
                    items = [m for m in items if not self._is_auto_extract_note(m)]
            elif ws_name:
                items = agent.memory.list_recent_notes_by_scopes(
                    scopes=["global", f"workspace:{ws_name}"],
                    limit=fetch_limit, offset=offset, exclude_tags=exclude_tags,
                    include_kinds=include_kinds,
                )
            else:
                items = agent.list_memories(
                    limit=fetch_limit, offset=offset, exclude_tags=exclude_tags, include_kinds=include_kinds,
                )
            has_more = len(items) > limit
            if has_more:
                items = items[:limit]
            items = [self._normalize_note_payload(m) for m in items]
            payload = {"type": "memories", "items": items, "offset": offset, "has_more": has_more}
            if request_id is not None:
                payload["request_id"] = request_id
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "delete_memory":
            raw = data.get("note_id")
            note_id = str(raw).strip() if raw is not None else ""
            try:
                ok = self._gateway.base_agent.forget(note_id) if note_id else False
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
                items = self._gateway.memory.list_belief_models(scopes=scopes)
            except Exception as exc:
                log.error("list_belief_models failed: %s", exc, exc_info=True)
                items = []
            await ws.send(json.dumps({"type": "belief_models", "items": items}, default=str))
        elif msg_type == "list_profile_facts":
            try:
                items = self._gateway.memory.user_profile.list_facts(limit=200)
            except Exception as exc:
                log.error("list_profile_facts failed: %s", exc, exc_info=True)
                items = []
            await ws.send(json.dumps({"type": "profile_facts", "items": items}, default=str))
        elif msg_type == "delete_session":
            sid = data.get("session_id", "")
            ok = self._gateway.memory.delete_session(sid) if sid else False
            await ws.send(json.dumps({"type": "session_deleted", "session_id": sid, "ok": ok}))
        elif msg_type == "get_session_history":
            await self._handle_get_session_history(ws, data)
        elif msg_type == "search_sessions":
            await self._handle_search_sessions(ws, data)
        elif msg_type == "get_session_lineage":
            await self._handle_get_session_lineage(ws, data)
        elif msg_type == "get_learning_state":
            await self._handle_get_learning_state(ws, data)
        elif msg_type == "list_scheduled_tasks":
            tasks = self._gateway.memory.list_scheduled_tasks()
            await ws.send(json.dumps({"type": "scheduled_tasks", "tasks": tasks}, default=str))
        elif msg_type == "create_scheduled_task":
            mem = self._gateway.memory
            task_id = mem.add_scheduled_task(
                cron=data.get("cron", ""),
                prompt=data.get("prompt", ""),
                agent=data.get("agent", ""),
                run_once=bool(data.get("run_once", False)),
                title=data.get("title", ""),
            )
            tasks = mem.list_scheduled_tasks()
            task = next((t for t in tasks if t["id"] == task_id), None)
            await ws.send(json.dumps({"type": "task_created", "task": task}, default=str))
        elif msg_type == "toggle_scheduled_task":
            task_id = data.get("task_id", "")
            enabled = bool(data.get("enabled", True))
            ok = self._gateway.memory.toggle_scheduled_task(task_id, enabled)
            await ws.send(json.dumps({"type": "task_toggled", "task_id": task_id, "enabled": enabled, "ok": ok}))
        elif msg_type == "run_scheduled_task_now":
            task_id = data.get("task_id", "")
            tasks = self._gateway.memory.list_scheduled_tasks()
            job = next((t for t in tasks if t["id"] == task_id), None)
            if job:
                asyncio.create_task(self._scheduler._run_job(job))
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": True}))
            else:
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": False}))
        elif msg_type == "delete_scheduled_task":
            task_id = data.get("task_id", "")
            ok = self._gateway.memory.delete_scheduled_task(task_id)
            await ws.send(json.dumps({"type": "task_cancelled", "task_id": task_id, "ok": ok}))
        elif msg_type == "list_todos":
            status = data.get("status") or None
            items = self._gateway.memory.list_todos(status=status)
            await ws.send(json.dumps({"type": "todos", "items": items}, default=str))
        elif msg_type == "create_todo":
            mem = self._gateway.memory
            due_at = data.get("due_at")
            item = mem.add_todo(
                title=data.get("title", ""),
                notes=data.get("notes", ""),
                priority=int(data.get("priority", 0)),
                due_at=int(due_at) if due_at else None,
                tags=data.get("tags") or [],
            )
            await ws.send(json.dumps({"type": "todo_created", "item": item}, default=str))
        elif msg_type == "update_todo":
            mem = self._gateway.memory
            todo_id = data.get("todo_id", "")
            fields = {k: v for k, v in data.items() if k not in ("type", "todo_id")}
            item = mem.update_todo(todo_id, **fields)
            if item:
                await ws.send(json.dumps({"type": "todo_updated", "item": item}, default=str))
            else:
                await ws.send(json.dumps({"type": "error", "message": f"Todo not found: {todo_id}"}))
        elif msg_type == "delete_todo":
            todo_id = data.get("todo_id", "")
            ok = self._gateway.memory.delete_todo(todo_id)
            await ws.send(json.dumps({"type": "todo_deleted", "todo_id": todo_id, "ok": ok}))
        elif msg_type == "list_calendar_events":
            await self._handle_list_calendar_events(ws, data)
        elif msg_type == "create_calendar_event":
            await self._handle_create_calendar_event(ws, data)
        elif msg_type == "update_calendar_event":
            await self._handle_update_calendar_event(ws, data)
        elif msg_type == "delete_calendar_event":
            await self._handle_delete_calendar_event(ws, data)
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
        elif msg_type == "list_models":
            await self._handle_list_models(ws, data)
        elif msg_type == "check_update":
            await self._handle_check_update(ws, data)
        elif msg_type == "run_update":
            await self._handle_run_update(ws, data)
        elif msg_type == "file_upload":
            await self._ws_handle_upload(ws, data)
        elif msg_type == "list_skills":
            await self._handle_list_skills(ws)
        elif msg_type == "save_skill":
            await self._handle_save_skill(ws, data)
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
