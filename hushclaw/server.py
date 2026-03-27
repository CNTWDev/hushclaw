"""HushClaw WebSocket server — requires 'websockets>=12.0' (pip install hushclaw[server])."""
from __future__ import annotations

import asyncio
import json
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _make_response(status: HTTPStatus, headers: list, body: bytes):
    """Build a websockets Response compatible with websockets 12–15."""
    try:
        # websockets ≥ 13: Response is a proper dataclass in websockets.http11
        from websockets.http11 import Response as _Response
        from websockets.datastructures import Headers as _Headers
        hdr = _Headers(dict(headers))
        return _Response(status.value, status.phrase, hdr, body)
    except Exception:
        # Fallback: try the legacy connection.respond path (caller handles it)
        raise

from hushclaw.config.schema import ServerConfig
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger

log = get_logger("server")

_WEB_DIR = Path(__file__).parent / "web"
_MIME = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
}


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


def _dict_to_toml(data: dict) -> str:
    """Minimal TOML serializer supporting scalars, scalar lists, subsections, and arrays-of-tables."""
    lines: list[str] = []

    def _scalar(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'"{escaped}"'
        if isinstance(v, float):
            return repr(v)
        return str(v)

    def _list_val(lst: list) -> str:
        parts = []
        for item in lst:
            s = _scalar(item)
            if s is not None:
                parts.append(s)
        return "[" + ", ".join(parts) + "]"

    # Top-level scalars first
    for k, v in data.items():
        if not isinstance(v, (dict, list)):
            s = _scalar(v)
            if s is not None:
                lines.append(f"{k} = {s}")
        elif isinstance(v, list) and all(not isinstance(i, dict) for i in v):
            lines.append(f"{k} = {_list_val(v)}")

    # Sections: [k] scalars → [k.sk] subsections → [[k.sk]] arrays-of-tables
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        lines.append(f"\n[{k}]")
        # Scalar and scalar-list keys within the section
        for sk, sv in v.items():
            if isinstance(sv, list) and sv and all(isinstance(i, dict) for i in sv):
                pass  # arrays-of-tables handled below
            elif isinstance(sv, dict):
                pass  # subsection handled below
            elif isinstance(sv, list):
                lines.append(f"{sk} = {_list_val(sv)}")
            else:
                s = _scalar(sv)
                if s is not None:
                    lines.append(f"{sk} = {s}")
        # Subsections [k.sk]
        for sk, sv in v.items():
            if isinstance(sv, dict):
                lines.append(f"\n[{k}.{sk}]")
                for ik, iv in sv.items():
                    if isinstance(iv, list) and all(not isinstance(i, dict) for i in iv):
                        lines.append(f"{ik} = {_list_val(iv)}")
                    elif not isinstance(iv, (dict, list)):
                        s = _scalar(iv)
                        if s is not None:
                            lines.append(f"{ik} = {s}")
        # Arrays-of-tables [[k.sk]]
        for sk, sv in v.items():
            if isinstance(sv, list) and sv and all(isinstance(i, dict) for i in sv):
                for item in sv:
                    lines.append(f"\n[[{k}.{sk}]]")
                    for ik, iv in item.items():
                        if isinstance(iv, list) and all(not isinstance(i, dict) for i in iv):
                            lines.append(f"{ik} = {_list_val(iv)}")
                        elif not isinstance(iv, (dict, list)):
                            s = _scalar(iv)
                            if s is not None:
                                lines.append(f"{ik} = {s}")

    return "\n".join(lines) + "\n"


class HushClawServer:
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

    def __init__(self, gateway, config: ServerConfig) -> None:
        self._gateway = gateway
        self._config = config
        self._skill_repo_cache: list | None = None
        self._skill_repo_cache_time: float = 0.0
        # Webhook handlers registered by connectors: path → async callable(path, query, body)
        self._webhook_handlers: dict[str, any] = {}
        # Track connected WS clients for broadcast (config_reloaded etc.)
        self._connected_clients: set = set()
        # Config file watcher state
        self._config_file_path: Path | None = None
        self._config_file_mtime: float = 0.0
        self._config_watcher_task: asyncio.Task | None = None
        # Category cache (skill marketplace)
        self._category_cache: list = []
        self._category_cache_time: float = 0.0

        # File upload directory (resolved from config or data_dir/uploads)
        upload_dir = config.upload_dir
        if upload_dir is None:
            upload_dir = gateway._base_agent.config.memory.data_dir / "uploads"
        self._upload_dir: Path = Path(upload_dir)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        from hushclaw.scheduler import Scheduler
        memory = gateway._base_agent.memory
        self._scheduler = Scheduler(memory, gateway)
        # Inject scheduler into all agents so tools can reference it
        for pool in gateway._pools.values():
            pool._agent._scheduler = self._scheduler

        from hushclaw.connectors.manager import ConnectorsManager
        self._connectors = ConnectorsManager(
            gateway._base_agent.config.connectors,
            gateway,
            webhook_registry=self._webhook_handlers,
        )

    async def start(self) -> None:
        try:
            from websockets.asyncio.server import serve as _ws_serve
        except ImportError:
            raise ImportError(
                "websockets>=12.0 is required for 'hushclaw serve'. "
                "Install with: pip install 'hushclaw[server]'"
            ) from None

        log.info(
            "Starting HushClaw server on %s:%d",
            self._config.host, self._config.port,
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
        ):
            print(
                f"HushClaw server listening on "
                f"http://{self._config.host}:{self._config.port}"
            )
            if self._config.api_key:
                print("API key authentication enabled (X-API-Key header).")
            await self._scheduler.start()
            await self._connectors.start()
            await self._start_config_watcher()
            try:
                await asyncio.Future()  # run forever
            finally:
                if self._config_watcher_task:
                    self._config_watcher_task.cancel()
                await self._connectors.stop()
                await self._scheduler.stop()

    # ── Config file watcher ────────────────────────────────────────────────

    async def _start_config_watcher(self) -> None:
        """Start a background task that polls the config file every 15 seconds."""
        from hushclaw.config.loader import get_config_dir
        self._config_file_path = get_config_dir() / "hushclaw.toml"
        try:
            self._config_file_mtime = self._config_file_path.stat().st_mtime
        except OSError:
            self._config_file_mtime = 0.0
        self._config_watcher_task = asyncio.create_task(self._config_watcher_loop())

    async def _config_watcher_loop(self) -> None:
        """Poll config file mtime every 15 seconds; reload and notify on change."""
        while True:
            await asyncio.sleep(15)
            try:
                mtime = self._config_file_path.stat().st_mtime
            except OSError:
                continue
            if mtime != self._config_file_mtime:
                self._config_file_mtime = mtime
                log.info("Config file changed — hot-reloading non-critical fields")
                try:
                    self._apply_config()
                    msg = json.dumps({"type": "config_reloaded", "source": "file_watcher"})
                    dead: set = set()
                    for ws in list(self._connected_clients):
                        try:
                            await ws.send(msg)
                        except Exception:
                            dead.add(ws)
                    self._connected_clients -= dead
                except Exception as e:
                    log.error("Config watcher reload failed: %s", e)

    async def _http_handler(self, connection, request):
        """websockets asyncio process_request hook: serve static files, webhooks, WS upgrades."""
        try:
            if request.headers.get("upgrade", "").lower() == "websocket":
                return None  # let websockets handle WS upgrade normally

            full_path = request.path
            path      = full_path.split("?")[0]
            query     = full_path.split("?", 1)[1] if "?" in full_path else ""
            method    = getattr(request, "method", "GET").upper()

            # ── File upload (PUT /upload?name=filename) ────────────────────
            if path == "/upload" and method == "PUT":
                return await self._handle_upload(connection, request, query)

            # ── File download (GET /files/<file_id_name>) ──────────────────
            if path.startswith("/files/") and method == "GET":
                return await self._serve_file(request, query, path[7:])

            # ── Webhook routing (POST /webhook/<platform>) ─────────────────
            if path.startswith("/webhook/"):
                platform = path[9:]  # strip "/webhook/"
                handler  = self._webhook_handlers.get(platform)
                if handler:
                    # Read request body — websockets ≥13 provides request.body;
                    # fall back to reading directly from the connection reader.
                    body = getattr(request, "body", None)
                    if body is None:
                        cl = int(request.headers.get("Content-Length", 0))
                        if cl > 0:
                            try:
                                body = await asyncio.wait_for(
                                    connection.reader.read(cl), timeout=5
                                )
                            except Exception:
                                body = b""
                        else:
                            body = b""
                    try:
                        status_code, resp_body = await handler(path, query, body)
                    except Exception as exc:
                        log.error("Webhook handler error (%s): %s", platform, exc)
                        return _make_response(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            [("Connection", "close")], b"handler error"
                        )
                    status = HTTPStatus(status_code)
                    return _make_response(status, [
                        ("Content-Type",   "text/plain"),
                        ("Content-Length", str(len(resp_body))),
                        ("Connection",     "close"),
                    ], resp_body)
                return _make_response(
                    HTTPStatus.NOT_FOUND, [("Connection", "close")], b"no handler"
                )

            # ── Static file serving ────────────────────────────────────────
            if path == "/":
                path = "/index.html"
            file_path = _WEB_DIR / path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                suffix = file_path.suffix
                mime   = _MIME.get(suffix, "application/octet-stream")
                body   = file_path.read_bytes()
                cache_control = "no-store" if suffix == ".html" else "no-cache, must-revalidate"
                return _make_response(HTTPStatus.OK, [
                    ("Content-Type",   mime),
                    ("Cache-Control", cache_control),
                    ("Content-Length", str(len(body))),
                    ("Connection",     "close"),
                ], body)
            return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")
        except Exception as exc:
            log.error("HTTP handler error: %s", exc, exc_info=True)
            try:
                return _make_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR, [("Connection", "close")], b"Server error"
                )
            except Exception:
                return None

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
        session_ids: dict[str, str] = {}  # agent_name → session_id
        active_tasks: dict[str, asyncio.Task] = {}  # session_id → background Task

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
                    task = active_tasks.pop(sid, None)
                    if task and not task.done():
                        task.cancel()
                    await ws.send(json.dumps({"type": "stopped", "session_id": sid}))

                elif msg_type == "browser_handover_done":
                    sid = data.get("session_id", "")
                    event = self._gateway.handover_registry.get(sid)
                    if event:
                        event.set()

                elif msg_type in ("chat", "pipeline", "orchestrate"):
                    # Resolve session_id now (before the task runs) so stop can cancel it.
                    # _handle_chat will also assign to session_ids[agent] inside the task.
                    agent = data.get("agent", "default")
                    from hushclaw.util.ids import make_id
                    sid = data.get("session_id") or session_ids.get(agent) or make_id("s-")
                    # Pre-populate so the client's subsequent stop message finds the task.
                    if msg_type == "chat" and not data.get("session_id"):
                        data = dict(data)
                        data["session_id"] = sid
                    task = asyncio.create_task(
                        self._dispatch(ws, data, session_ids)
                    )
                    active_tasks[sid] = task
                    task.add_done_callback(lambda t, s=sid: active_tasks.pop(s, None))

                else:
                    await self._dispatch(ws, data, session_ids)

        except Exception as e:
            log.debug("Client %s disconnected: %s", remote, e)
        finally:
            self._connected_clients.discard(ws)
            for task in active_tasks.values():
                task.cancel()
            log.info("Client disconnected: %s", remote)

    async def _dispatch(self, ws, data: dict, session_ids: dict) -> None:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type == "chat":
            await self._handle_chat(ws, data, session_ids)
        elif msg_type == "pipeline":
            await self._handle_pipeline(ws, data, session_ids)
        elif msg_type == "run_hierarchical":
            await self._handle_run_hierarchical(ws, data, session_ids)
        elif msg_type == "orchestrate":
            await self._handle_orchestrate(ws, data, session_ids)
        elif msg_type == "list_agents":
            await ws.send(json.dumps({"type": "agents", "items": self._gateway.list_agents()}))
        elif msg_type == "create_agent":
            name = data.get("name", "")
            try:
                self._gateway.create_agent(
                    name=name,
                    description=data.get("description", ""),
                    model=data.get("model", ""),
                    system_prompt=data.get("system_prompt", ""),
                    instructions=data.get("instructions", ""),
                    role=data.get("role", "specialist"),
                    team=data.get("team", ""),
                    reports_to=data.get("reports_to", ""),
                    capabilities=data.get("capabilities", []) or [],
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
                    model=data.get("model"),
                    system_prompt=data.get("system_prompt"),
                    instructions=data.get("instructions"),
                    role=data.get("role"),
                    team=data.get("team"),
                    reports_to=data.get("reports_to"),
                    capabilities=data.get("capabilities"),
                )
                await ws.send(json.dumps({
                    "type": "agent_updated",
                    "name": name,
                    "agents": self._gateway.list_agents(),
                }))
            except ValueError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif msg_type == "list_sessions":
            gw_cfg = self._gateway._base_agent.config.gateway
            limit = int(data.get("limit", gw_cfg.session_list_limit))
            include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
            max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
            items = self._gateway._base_agent.memory.list_sessions(
                limit=max(1, limit),
                include_scheduled=bool(include_scheduled),
                max_idle_days=max(0, max_idle_days),
            )
            await ws.send(json.dumps({"type": "sessions", "items": items}, default=str))
        elif msg_type == "list_memories":
            query = data.get("query", "")
            limit = int(data.get("limit", 20))
            agent = self._gateway._base_agent
            items = agent.search(query, limit=limit) if query else agent.list_memories(limit=limit)
            await ws.send(json.dumps({"type": "memories", "items": items}, default=str))
        elif msg_type == "delete_memory":
            note_id = data.get("note_id", "")
            ok = self._gateway._base_agent.forget(note_id)
            await ws.send(json.dumps({"type": "memory_deleted", "note_id": note_id, "ok": ok}))
        elif msg_type == "delete_session":
            sid = data.get("session_id", "")
            ok = self._gateway._base_agent.memory.delete_session(sid) if sid else False
            await ws.send(json.dumps({"type": "session_deleted", "session_id": sid, "ok": ok}))
        elif msg_type == "get_session_history":
            sid = data.get("session_id", "")
            turns = self._gateway._base_agent.memory.load_session_turns(sid)
            await ws.send(json.dumps({"type": "session_history", "session_id": sid, "turns": turns}, default=str))
        elif msg_type == "list_scheduled_tasks":
            tasks = self._gateway._base_agent.memory.list_scheduled_tasks()
            await ws.send(json.dumps({"type": "scheduled_tasks", "tasks": tasks}, default=str))
        elif msg_type == "create_scheduled_task":
            mem = self._gateway._base_agent.memory
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
            ok = self._gateway._base_agent.memory.toggle_scheduled_task(task_id, enabled)
            await ws.send(json.dumps({"type": "task_toggled", "task_id": task_id, "enabled": enabled, "ok": ok}))
        elif msg_type == "run_scheduled_task_now":
            task_id = data.get("task_id", "")
            tasks = self._gateway._base_agent.memory.list_scheduled_tasks()
            job = next((t for t in tasks if t["id"] == task_id), None)
            if job:
                asyncio.create_task(self._scheduler._run_job(job))
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": True}))
            else:
                await ws.send(json.dumps({"type": "task_triggered", "task_id": task_id, "ok": False}))
        elif msg_type == "delete_scheduled_task":
            task_id = data.get("task_id", "")
            ok = self._gateway._base_agent.memory.delete_scheduled_task(task_id)
            await ws.send(json.dumps({"type": "task_cancelled", "task_id": task_id, "ok": ok}))
        elif msg_type == "list_todos":
            status = data.get("status") or None
            items = self._gateway._base_agent.memory.list_todos(status=status)
            await ws.send(json.dumps({"type": "todos", "items": items}, default=str))
        elif msg_type == "create_todo":
            mem = self._gateway._base_agent.memory
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
            mem = self._gateway._base_agent.memory
            todo_id = data.get("todo_id", "")
            fields = {k: v for k, v in data.items() if k not in ("type", "todo_id")}
            item = mem.update_todo(todo_id, **fields)
            if item:
                await ws.send(json.dumps({"type": "todo_updated", "item": item}, default=str))
            else:
                await ws.send(json.dumps({"type": "error", "message": f"Todo not found: {todo_id}"}))
        elif msg_type == "delete_todo":
            todo_id = data.get("todo_id", "")
            ok = self._gateway._base_agent.memory.delete_todo(todo_id)
            await ws.send(json.dumps({"type": "todo_deleted", "todo_id": todo_id, "ok": ok}))
        elif msg_type == "get_config_status":
            await ws.send(json.dumps(self._config_status()))
        elif msg_type == "save_config":
            await self._handle_save_config(ws, data)
        elif msg_type == "test_provider":
            await self._handle_test_provider(ws, data)
        elif msg_type == "list_models":
            await self._handle_list_models(ws, data)
        elif msg_type == "file_upload":
            await self._ws_handle_upload(ws, data)
        elif msg_type == "list_skills":
            await self._handle_list_skills(ws)
        elif msg_type == "list_skill_repos":
            await self._handle_list_skill_repos(ws)
        elif msg_type == "install_skill_repo":
            await self._handle_install_skill_repo(ws, data)
        elif msg_type == "publish_skill":
            await self._handle_publish_skill(ws, data)
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _config_status(self) -> dict:
        """Return current configuration state for the setup wizard."""
        cfg = self._gateway._base_agent.config
        provider = cfg.provider.name
        api_key = cfg.provider.api_key
        needs_key = "ollama" not in provider

        api_key_masked = ""
        if api_key:
            api_key_masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) > 8 else "set"

        from hushclaw.config.loader import get_config_dir
        cfg_file = str(get_config_dir() / "hushclaw.toml")

        c   = cfg.connectors
        tg  = c.telegram
        fs  = c.feishu
        dc  = c.discord
        sl  = c.slack
        dt  = c.dingtalk
        wc  = c.wecom
        return {
            "type": "config_status",
            "configured": (not needs_key) or bool(api_key),
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "max_tool_rounds": cfg.agent.max_tool_rounds,
            "system_prompt": cfg.agent.system_prompt,
            "cost_per_1k_input_tokens": cfg.provider.cost_per_1k_input_tokens,
            "cost_per_1k_output_tokens": cfg.provider.cost_per_1k_output_tokens,
            "config_file": cfg_file,
            "connectors": {
                "telegram": {
                    "enabled":         tg.enabled,
                    "bot_token_set":   bool(tg.bot_token),
                    "agent":           tg.agent,
                    "allowlist":       tg.allowlist,
                    "group_allowlist": tg.group_allowlist,
                    "group_policy":    tg.group_policy,
                    "require_mention": tg.require_mention,
                    "stream":          tg.stream,
                },
                "feishu": {
                    "enabled":                fs.enabled,
                    "app_id":                 fs.app_id,
                    "app_secret_set":         bool(fs.app_secret),
                    "encrypt_key_set":        bool(fs.encrypt_key),
                    "verification_token_set": bool(fs.verification_token),
                    "agent":                  fs.agent,
                    "allowlist":              fs.allowlist,
                    "stream":                 fs.stream,
                },
                "discord": {
                    "enabled":         dc.enabled,
                    "bot_token_set":   bool(dc.bot_token),
                    "agent":           dc.agent,
                    "allowlist":       dc.allowlist,
                    "guild_allowlist": dc.guild_allowlist,
                    "require_mention": dc.require_mention,
                    "stream":          dc.stream,
                },
                "slack": {
                    "enabled":       sl.enabled,
                    "bot_token_set": bool(sl.bot_token),
                    "app_token_set": bool(sl.app_token),
                    "agent":         sl.agent,
                    "allowlist":     sl.allowlist,
                    "stream":        sl.stream,
                },
                "dingtalk": {
                    "enabled":           dt.enabled,
                    "client_id":         dt.client_id,
                    "client_secret_set": bool(dt.client_secret),
                    "agent":             dt.agent,
                    "allowlist":         dt.allowlist,
                    "stream":            dt.stream,
                },
                "wecom": {
                    "enabled":          wc.enabled,
                    "corp_id":          wc.corp_id,
                    "corp_secret_set":  bool(wc.corp_secret),
                    "agent_id":         wc.agent_id,
                    "token_set":        bool(wc.token),
                    "agent":            wc.agent,
                    "allowlist":        wc.allowlist,
                },
            },
            "browser": {
                "enabled":              cfg.browser.enabled,
                "headless":             cfg.browser.headless,
                "timeout":              cfg.browser.timeout,
                "playwright_installed": self._check_playwright(),
            },
            "email": {
                "enabled":      cfg.email.enabled,
                "imap_host":    cfg.email.imap_host,
                "imap_port":    cfg.email.imap_port,
                "smtp_host":    cfg.email.smtp_host,
                "smtp_port":    cfg.email.smtp_port,
                "username":     cfg.email.username,
                "password_set": bool(cfg.email.password),
                "mailbox":      cfg.email.mailbox,
            },
            "calendar": {
                "enabled":       cfg.calendar.enabled,
                "url":           cfg.calendar.url,
                "username":      cfg.calendar.username,
                "password_set":  bool(cfg.calendar.password),
                "calendar_name": cfg.calendar.calendar_name,
            },
            "context": {
                "history_budget":        cfg.context.history_budget,
                "compact_threshold":     cfg.context.compact_threshold,
                "compact_keep_turns":    cfg.context.compact_keep_turns,
                "compact_strategy":      cfg.context.compact_strategy,
                "memory_min_score":      cfg.context.memory_min_score,
                "memory_max_tokens":     cfg.context.memory_max_tokens,
                "auto_extract":          cfg.context.auto_extract,
                "memory_decay_rate":     cfg.context.memory_decay_rate,
                "retrieval_temperature": cfg.context.retrieval_temperature,
                "serendipity_budget":    cfg.context.serendipity_budget,
            },
            "skill_dir":      str(cfg.tools.skill_dir or ""),
            "user_skill_dir": str(cfg.tools.user_skill_dir or ""),
        }

    async def _handle_save_config(self, ws, data: dict) -> None:
        """Write wizard-supplied config to the user config TOML file."""
        from hushclaw.config.loader import get_config_dir, _load_toml

        incoming: dict = data.get("config", {})
        cfg_dir = get_config_dir()
        cfg_file = cfg_dir / "hushclaw.toml"

        try:
            existing: dict = _load_toml(cfg_file)
        except Exception:
            existing = {}

        # Deep-merge only the sections the wizard touched
        for section in ("provider", "agent", "context", "server", "email", "calendar"):
            if section in incoming and isinstance(incoming[section], dict):
                sec = existing.setdefault(section, {})
                for k, v in incoming[section].items():
                    # Strip whitespace from string values (guards against copy-paste
                    # trailing newlines in keys — would cause "Missing Authentication header").
                    if isinstance(v, str):
                        v = v.strip()
                    # Allow clearing provider.base_url explicitly. Other empty
                    # strings are treated as "unchanged" wizard fields.
                    if k == "base_url":
                        sec[k] = v
                        continue
                    if v != "":          # skip empty strings (wizard left blank)
                        sec[k] = v

        # Tools section (user_skill_dir)
        if "tools" in incoming and isinstance(incoming["tools"], dict):
            tools_sec = existing.setdefault("tools", {})
            for k, v in incoming["tools"].items():
                if isinstance(v, str):
                    v = v.strip()
                tools_sec[k] = v  # allow empty string to clear user_skill_dir

        # Browser section
        if "browser" in incoming and isinstance(incoming["browser"], dict):
            br_sec = existing.setdefault("browser", {})
            for k, v in incoming["browser"].items():
                if isinstance(v, (bool, int)):
                    br_sec[k] = v
                elif isinstance(v, str) and v != "":
                    br_sec[k] = v

        # Connectors — one extra nesting level per platform
        if "connectors" in incoming and isinstance(incoming["connectors"], dict):
            conn_sec = existing.setdefault("connectors", {})
            for platform in ("telegram", "feishu", "discord", "slack", "dingtalk", "wecom"):
                plat_in = incoming["connectors"].get(platform)
                if not isinstance(plat_in, dict):
                    continue
                plat_sec = conn_sec.setdefault(platform, {})
                for k, v in plat_in.items():
                    if isinstance(v, str):
                        v = v.strip()
                    # booleans, ints, and lists always overwrite; empty strings are skipped
                    if isinstance(v, (bool, int, list)):
                        plat_sec[k] = v
                    elif v != "":
                        plat_sec[k] = v

        try:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(_dict_to_toml(existing), encoding="utf-8")
            self._apply_config()
            await ws.send(json.dumps({
                "type": "config_saved",
                "ok": True,
                "config_file": str(cfg_file),
                "restart_required": False,
            }))
        except Exception as e:
            log.error("save_config error: %s", e, exc_info=True)
            await ws.send(json.dumps({
                "type": "config_saved",
                "ok": False,
                "error": str(e),
            }))

    def _apply_config(self) -> None:
        """Hot-reload provider and config on the running agent after a config save."""
        try:
            from hushclaw.config.loader import load_config
            from hushclaw.providers.registry import get_provider
            new_cfg = load_config()
            agent = self._gateway._base_agent
            agent.config = new_cfg
            agent.provider = get_provider(new_cfg.provider)
            # Rebuild tool registry so browser_enabled changes take effect immediately
            agent.registry._tools = {}
            agent.registry._plugin_tools.clear()
            agent.registry._skill_tools.clear()
            agent.registry.load_builtins(
                enabled=None,  # filter applied after all sources
                browser_enabled=new_cfg.browser.enabled,
            )
            if new_cfg.tools.plugin_dir:
                agent.registry.load_plugins(new_cfg.tools.plugin_dir)
            skill_dir = new_cfg.tools.skill_dir
            if skill_dir and skill_dir.exists():
                for tools_dir in skill_dir.glob("*/tools"):
                    if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                        agent.registry.load_plugins(tools_dir)
            user_skill_dir = new_cfg.tools.user_skill_dir
            if user_skill_dir and user_skill_dir.exists():
                for tools_dir in user_skill_dir.glob("*/tools"):
                    if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                        skill_name = tools_dir.parent.name
                        agent.registry.load_plugins(tools_dir, namespace=skill_name)
            agent.registry.apply_profile(new_cfg.tools.profile)
            agent.registry.apply_enabled_filter(new_cfg.tools.enabled)
            # Agent collaboration tools are registered separately from load_builtins;
            # re-register after rebuild so hot-reload keeps update_agent/list_agents.
            agent.enable_agent_tools()
            # Flush all cached AgentLoop sessions so the next request creates a
            # fresh loop bound to the new provider/config (old loops hold a
            # reference to the previous provider object and would keep using it).
            for pool in self._gateway._pools.values():
                pool._loops.clear()
                pool._loop_last_used.clear()
            log.info(
                "Config reloaded: provider=%s model=%s (session cache flushed)",
                new_cfg.provider.name, new_cfg.agent.model,
            )
        except Exception as exc:
            log.error("Config reload error: %s", exc, exc_info=True)

    async def _handle_list_models(self, ws, data: dict) -> None:
        from hushclaw.config.schema import ProviderConfig
        from hushclaw.providers.registry import get_provider
        base_cfg = self._gateway._base_agent.config.provider
        cfg = ProviderConfig(
            name=data.get("provider") or base_cfg.name,
            api_key=data.get("api_key") or base_cfg.api_key,
            base_url=data.get("base_url") or base_cfg.base_url,
        )
        try:
            provider = get_provider(cfg)
            models = await provider.list_models()
            await ws.send(json.dumps({"type": "models", "items": models}))
        except Exception as e:
            await ws.send(json.dumps({"type": "models", "items": [], "error": str(e)}))

    async def _handle_test_provider(self, ws, data: dict) -> None:
        import socket
        import ssl
        import time
        import urllib.error
        import urllib.request
        from urllib.parse import urlparse

        loop = asyncio.get_event_loop()

        async def step(step_id: str, status: str, label: str, detail: str = "") -> None:
            await ws.send(json.dumps({
                "type": "test_provider_step",
                "step": step_id, "status": status,
                "label": label, "detail": detail,
            }))

        async def finish(ok: bool, detail: str = "") -> None:
            await ws.send(json.dumps({"type": "test_provider_result", "ok": ok, "detail": detail}))

        base_cfg = self._gateway._base_agent.config.provider
        base_url  = (data.get("base_url") or base_cfg.base_url or "").strip().rstrip("/")
        api_key   = (data.get("api_key")  or base_cfg.api_key  or "").strip()
        provider_name = (data.get("provider") or base_cfg.name or "").strip()
        model     = (data.get("model") or self._gateway._base_agent.config.agent.model or "").strip()

        if not base_url:
            await finish(False, "Base URL is empty.")
            return

        parsed   = urlparse(base_url)
        host     = parsed.hostname or ""
        port     = parsed.port or (443 if parsed.scheme == "https" else 80)
        is_https = parsed.scheme == "https"

        if not host:
            await finish(False, f"Cannot parse host from URL: {base_url}")
            return

        # ── Step 1: DNS resolution ────────────────────────────────────────────
        await step("dns", "running", "DNS Resolution", f"Resolving {host}…")
        try:
            t0 = time.monotonic()
            addrs = await loop.run_in_executor(
                None, lambda: socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            )
            ip = addrs[0][4][0]
            ms = int((time.monotonic() - t0) * 1000)
            await step("dns", "ok", "DNS Resolution", f"{host} → {ip}  ({ms} ms)")
        except socket.gaierror as e:
            await step("dns", "error", "DNS Resolution",
                       f"Cannot resolve '{host}': {e}. Check hostname / network / VPN.")
            await finish(False, "DNS resolution failed.")
            return

        # ── Step 2: TCP connectivity ──────────────────────────────────────────
        await step("tcp", "running", "TCP Connect", f"Connecting to {ip}:{port}…")
        try:
            t0 = time.monotonic()
            def _tcp():
                s = socket.create_connection((host, port), timeout=6)
                s.close()
            await asyncio.wait_for(loop.run_in_executor(None, _tcp), timeout=8)
            ms = int((time.monotonic() - t0) * 1000)
            await step("tcp", "ok", "TCP Connect", f"Connected  ({ms} ms)")
        except (asyncio.TimeoutError, socket.timeout):
            await step("tcp", "error", "TCP Connect",
                       f"Timed out connecting to {host}:{port}. Firewall or wrong port?")
            await finish(False, "TCP connection timed out.")
            return
        except ConnectionRefusedError:
            await step("tcp", "error", "TCP Connect",
                       f"Connection refused on port {port}. Service may be down.")
            await finish(False, "Connection refused.")
            return
        except OSError as e:
            await step("tcp", "error", "TCP Connect", f"Network error: {e}")
            await finish(False, "TCP connection failed.")
            return

        # ── Step 3: TLS / certificate ─────────────────────────────────────────
        if is_https:
            await step("tls", "running", "TLS Handshake", "Verifying SSL certificate…")
            try:
                def _tls():
                    ctx = ssl.create_default_context()
                    with socket.create_connection((host, port), timeout=6) as raw:
                        with ctx.wrap_socket(raw, server_hostname=host) as ssock:
                            return ssock.getpeercert()
                cert = await asyncio.wait_for(loop.run_in_executor(None, _tls), timeout=8)
                expiry = cert.get("notAfter", "unknown")
                await step("tls", "ok", "TLS Handshake", f"Certificate valid · expires {expiry}")
            except ssl.SSLCertVerificationError as e:
                await step("tls", "warn", "TLS Handshake",
                           f"Certificate verification failed (self-signed?): {e}. Proceeding…")
            except ssl.SSLError as e:
                await step("tls", "error", "TLS Handshake", f"TLS error: {e}")
                await finish(False, "TLS handshake failed.")
                return
            except Exception as e:
                await step("tls", "warn", "TLS Handshake", f"Could not inspect certificate: {e}")
        else:
            await step("tls", "skip", "TLS Handshake", "HTTP (plain) — skipped")

        # ── Step 4: API endpoint reachability ─────────────────────────────────
        await step("api", "running", "API Endpoint", f"Probing {base_url}…")
        try:
            def _http_probe():
                req = urllib.request.Request(base_url, method="GET",
                                             headers={"User-Agent": "HushClaw/1.0"})
                try:
                    with urllib.request.urlopen(req, timeout=8) as r:
                        return r.status, ""
                except urllib.error.HTTPError as e:
                    return e.code, e.reason
            status, reason = await asyncio.wait_for(
                loop.run_in_executor(None, _http_probe), timeout=10
            )
            if status < 500:
                await step("api", "ok", "API Endpoint", f"HTTP {status} — endpoint reachable")
            else:
                await step("api", "warn", "API Endpoint",
                           f"HTTP {status} {reason} — server error, but host is up")
        except Exception as e:
            await step("api", "warn", "API Endpoint",
                       f"Probe returned unexpected result: {e}. Continuing anyway…")

        # ── Step 5: Authentication & model list ───────────────────────────────
        if not api_key and provider_name != "ollama":
            await step("auth", "skip", "API Authentication", "No API key provided — skipped")
            await finish(True, "Network checks passed. Enter an API key to validate authentication.")
            return

        await step("auth", "running", "API Authentication", "Verifying API key…")
        try:
            from hushclaw.config.schema import ProviderConfig
            from hushclaw.providers.registry import get_provider
            from hushclaw.providers.base import Message as LLMMessage

            cfg = ProviderConfig(
                name=provider_name,
                api_key=api_key,
                base_url=base_url,
                timeout=10,
                max_retries=0,
            )
            provider = get_provider(cfg)
            models = await asyncio.wait_for(provider.list_models(), timeout=10.0)

            if models:
                await step("auth", "ok", "API Authentication",
                           f"Authenticated · {len(models)} model(s) available")
            else:
                # list_models not implemented — try a 1-token completion
                await provider.complete(
                    messages=[LLMMessage(role="user", content="hi")],
                    system="", max_tokens=1, model=model or None,
                )
                await step("auth", "ok", "API Authentication", "Authenticated")
                models = []

        except asyncio.TimeoutError:
            await step("auth", "error", "API Authentication",
                       "Auth request timed out (10 s). API may be overloaded.")
            await finish(False, "Authentication timed out.")
            return
        except Exception as e:
            err = str(e)
            low = err.lower()
            if "401" in err or "unauthorized" in low:
                await step("auth", "error", "API Authentication",
                           f"Invalid or expired API key (401). Double-check your key.")
            elif "403" in err:
                await step("auth", "error", "API Authentication",
                           f"Access denied (403). Key may lack required permissions.")
            elif "429" in err or "rate" in low:
                await step("auth", "warn", "API Authentication",
                           f"Rate-limited (429) — key is valid but quota exceeded.")
                await finish(True, "All checks passed (rate-limited but key accepted).")
                return
            elif "404" in err:
                await step("auth", "error", "API Authentication",
                           f"Endpoint not found (404). Is the Base URL correct?")
            else:
                await step("auth", "error", "API Authentication", f"Auth failed: {err[:200]}")
            await finish(False, "Authentication failed.")
            return

        # ── Step 6: Model availability ────────────────────────────────────────
        if model and models:
            if any(m.get("id") == model or m == model for m in models):
                await step("model", "ok", "Model Check", f"'{model}' is available")
            else:
                ids = [m.get("id", m) if isinstance(m, dict) else m for m in models[:5]]
                await step("model", "warn", "Model Check",
                           f"'{model}' not found in model list. Available: {', '.join(str(i) for i in ids)}…")
        else:
            await step("model", "skip", "Model Check",
                       "Skipped (model list unavailable or no model specified)")

        await finish(True, "All checks passed.")

    async def _handle_list_skills(self, ws) -> None:
        agent = self._gateway._base_agent
        registry = getattr(agent, "_skill_registry", None)
        items = registry.list_all() if registry else []
        skill_dir = str(agent.config.tools.skill_dir or "")
        user_skill_dir = str(agent.config.tools.user_skill_dir or "")
        await ws.send(json.dumps({
            "type": "skills",
            "items": items,
            "skill_dir": skill_dir,
            "user_skill_dir": user_skill_dir,
            "configured": bool(skill_dir or user_skill_dir),
        }))

    # Primary index URL — static JSON hosted on GitHub, no rate limits.
    _INDEX_URL = (
        "https://raw.githubusercontent.com/CNTWDev/hushclaw-skills-index/main/index.json"
    )
    # GitHub Issues URL for publishing a new skill (prefilled template).
    _PUBLISH_ISSUE_URL = (
        "https://github.com/CNTWDev/hushclaw-skills-index/issues/new"
        "?template=add_skill.md&title=Add+skill%3A+{name}&body={body}"
    )

    # Curated skill repos always shown regardless of GitHub API availability.
    _CURATED_REPOS: list[dict] = [
        {
            "name": "VoltAgent/awesome-openclaw-skills",
            "url": "https://github.com/VoltAgent/awesome-openclaw-skills.git",
            "html_url": "https://github.com/VoltAgent/awesome-openclaw-skills",
            "stars": 0,
            "description": "Curated list of 5 000+ community OpenClaw skills — browse categories and install what you need.",
            "curated": True,
            "note": "Index repo — contains category markdown files, not SKILL.md files. Browse on GitHub for individual skill ideas.",
        },
    ]

    async def _fetch_index(self) -> list[dict]:
        """Fetch skills from the central index.json (no rate limits)."""
        import urllib.request
        from hushclaw.util.ssl_context import make_ssl_context

        req = urllib.request.Request(
            self._INDEX_URL,
            headers={"User-Agent": "HushClaw/1.0"},
        )
        loop = asyncio.get_event_loop()

        def _do_fetch():
            return urllib.request.urlopen(req, timeout=8, context=make_ssl_context()).read()

        raw = await loop.run_in_executor(None, _do_fetch)
        data = json.loads(raw)
        result = []
        for s in data.get("skills", []):
            result.append({
                "name":        s.get("name", ""),
                "url":         s.get("clone_url", s.get("repo_url", "")),
                "html_url":    s.get("html_url", s.get("repo_url", "")),
                "stars":       s.get("stars", 0),
                "description": s.get("description", ""),
                "author":      s.get("author", ""),
                "tags":        s.get("tags", []),
                "from_index":  True,
            })
        return result

    async def _fetch_categories(self) -> list[dict]:
        """Fetch category data from the awesome-openclaw-skills index.

        Returns list of ``{name, skills: [{name, url, description}]}`` dicts.
        Returns ``[]`` on any error (graceful degradation).
        """
        import urllib.request
        from hushclaw.util.ssl_context import make_ssl_context

        _CATEGORY_URL = (
            "https://raw.githubusercontent.com/VoltAgent/awesome-openclaw-skills/main/index.json"
        )
        req = urllib.request.Request(
            _CATEGORY_URL,
            headers={"User-Agent": "HushClaw/1.0"},
        )
        loop = asyncio.get_event_loop()

        def _do_fetch():
            return urllib.request.urlopen(req, timeout=8, context=make_ssl_context()).read()

        try:
            raw = await loop.run_in_executor(None, _do_fetch)
            data = json.loads(raw)
            categories = []
            for cat in data.get("categories", []):
                categories.append({
                    "name": cat.get("name", ""),
                    "skills": [
                        {
                            "name":        s.get("name", ""),
                            "url":         s.get("url", ""),
                            "description": s.get("description", ""),
                        }
                        for s in cat.get("skills", [])
                    ],
                })
            return categories
        except Exception as exc:
            log.debug("Category index fetch failed: %s", exc)
            return []

    async def _handle_list_skill_repos(self, ws) -> None:
        import time
        import urllib.request
        from hushclaw.util.ssl_context import make_ssl_context

        now = time.time()
        if self._skill_repo_cache is None or now - self._skill_repo_cache_time >= 300:
            index_repos: list = []
            github_repos: list = []
            error_msg = ""

            # Primary: central index.json (no rate limits)
            try:
                index_repos = await self._fetch_index()
                log.debug("Loaded %d skills from index.json", len(index_repos))
            except Exception as exc:
                log.debug("index.json fetch failed, falling back to GitHub Search: %s", exc)

            # Fallback: GitHub Search (60 req/h anonymous)
            if not index_repos:
                try:
                    api_url = (
                        "https://api.github.com/search/repositories"
                        "?q=hushclaw+skill+in:name,description,topics"
                        "&sort=stars&order=desc&per_page=6"
                    )
                    req = urllib.request.Request(
                        api_url,
                        headers={
                            "User-Agent": "HushClaw/1.0",
                            "Accept": "application/vnd.github.v3+json",
                        },
                    )
                    loop = asyncio.get_event_loop()

                    def _fetch_gh():
                        return urllib.request.urlopen(
                            req, timeout=8, context=make_ssl_context()
                        ).read()

                    raw = await loop.run_in_executor(None, _fetch_gh)
                    data_gh = json.loads(raw)
                    curated_names = {r["name"] for r in self._CURATED_REPOS}
                    github_repos = [
                        {
                            "name": r["full_name"],
                            "url": r["clone_url"],
                            "html_url": r["html_url"],
                            "stars": r["stargazers_count"],
                            "description": r.get("description") or "",
                        }
                        for r in data_gh.get("items", [])
                        if r["full_name"] not in curated_names
                    ]
                except Exception as exc:
                    error_msg = str(exc)
                    log.debug("GitHub skill repo search failed: %s", exc)

            # Merge: index repos take precedence; add curated + GitHub if index empty
            if index_repos:
                # Deduplicate against curated by name
                curated_names = {r["name"] for r in self._CURATED_REPOS}
                combined = list(self._CURATED_REPOS) + [
                    r for r in index_repos if r["name"] not in curated_names
                ]
            else:
                combined = list(self._CURATED_REPOS) + github_repos

            self._skill_repo_cache = combined
            self._skill_repo_cache_time = now
            if error_msg and not github_repos and not index_repos:
                log.debug("Showing curated repos only (all sources failed: %s)", error_msg)

        # Shallow-copy items so we can add 'installed' without mutating the cache
        skill_dir = self._gateway._base_agent.config.tools.skill_dir
        user_skill_dir = self._gateway._base_agent.config.tools.user_skill_dir
        repos_out = []
        for r in self._skill_repo_cache:
            item = dict(r)
            repo_name = r["url"].rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
            item["installed"] = (
                (skill_dir and (skill_dir / repo_name).exists()) or
                (user_skill_dir and (user_skill_dir / repo_name).exists())
            )
            repos_out.append(item)

        # Fetch categories (cached separately with same 5-min TTL)
        now2 = time.time()
        if not self._category_cache or now2 - self._category_cache_time >= 300:
            self._category_cache = await self._fetch_categories()
            self._category_cache_time = now2

        await ws.send(json.dumps({
            "type": "skill_repos",
            "items": repos_out,
            "categories": self._category_cache,
        }))

    async def _handle_install_skill_repo(self, ws, data: dict) -> None:
        import re

        url = data.get("url", "").strip()

        # Reject unsafe URLs: must be https://, no whitespace or shell metacharacters
        if not url.startswith("https://") or re.search(r'[\s$;|&<>`\'"\\]', url):
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "Invalid URL. Only plain HTTPS git URLs are supported.",
            }))
            return

        agent = self._gateway._base_agent
        skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
        if not skill_dir:
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "skill_dir is not configured. Add [tools] skill_dir = \"~/.hushclaw/skills\" or user_skill_dir = \"~/my-skills\" to hushclaw.toml.",
            }))
            return

        repo_name = url.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
        target_dir = skill_dir / repo_name

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)

            if target_dir.exists():
                await ws.send(json.dumps({
                    "type": "skill_install_progress",
                    "url": url,
                    "message": f"Updating {repo_name}…",
                }))
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", str(target_dir), "pull",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                await ws.send(json.dumps({
                    "type": "skill_install_progress",
                    "url": url,
                    "message": f"Cloning {repo_name}…",
                }))
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth=1", url, str(target_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await ws.send(json.dumps({
                    "type": "skill_install_result",
                    "ok": False,
                    "url": url,
                    "error": "Git operation timed out after 120 seconds.",
                }))
                return

            if proc.returncode != 0:
                lines = stderr.decode(errors="ignore").strip().splitlines()
                err = lines[-1] if lines else "Unknown git error"
                await ws.send(json.dumps({
                    "type": "skill_install_result",
                    "ok": False,
                    "url": url,
                    "error": err,
                }))
                return

            # Reload SkillRegistry so new skills are immediately available
            from hushclaw.skills.loader import SkillRegistry
            agent._skill_registry = SkillRegistry(skill_dir)
            # Invalidate marketplace cache so installed state refreshes
            self._skill_repo_cache = None

            # Count only skills from this repo (not built-ins)
            repo_skill_count = sum(
                1 for s in agent._skill_registry._skills.values()
                if str(target_dir) in s.get("path", "")
            )
            warning = ""
            if repo_skill_count == 0:
                warning = (
                    "No SKILL.md files found in this repo. "
                    "This may be an index/list repo rather than an installable skill pack. "
                    "Browse it on GitHub to find individual skills."
                )

            # Auto-install Python dependencies if requirements.txt is present
            deps_ok = None
            req_file = target_dir / "requirements.txt"
            if req_file.exists():
                await ws.send(json.dumps({
                    "type": "skill_install_progress",
                    "url": url,
                    "message": "Installing dependencies from requirements.txt…",
                }))
                pip_proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "-r", str(req_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(pip_proc.communicate(), timeout=120)
                    deps_ok = (pip_proc.returncode == 0)
                except asyncio.TimeoutError:
                    pip_proc.kill()
                    deps_ok = False
                    log.warning("pip install timed out for %s", req_file)

            # Load bundled tools from the newly installed repo's tools/ directory
            bundled_tool_count = 0
            tools_dir = target_dir / "tools"
            if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                before = len(agent.registry)
                agent.registry.load_plugins(tools_dir, namespace=repo_name)
                bundled_tool_count = len(agent.registry) - before

            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": True,
                "url": url,
                "repo": repo_name,
                "skill_count": len(agent._skill_registry),
                "repo_skill_count": repo_skill_count,
                "bundled_tool_count": bundled_tool_count,
                "deps_installed": deps_ok,
                "warning": warning,
            }))

        except Exception as exc:
            log.error("install_skill_repo error: %s", exc, exc_info=True)
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": str(exc),
            }))

    async def _handle_publish_skill(self, ws, data: dict) -> None:
        """Generate a GitHub issue URL so the user can publish their skill to the index."""
        from urllib.parse import quote

        skill_name = data.get("skill_name", "").strip()
        skill_desc = data.get("skill_description", "").strip()
        repo_url   = data.get("repo_url", "").strip()

        # If caller didn't supply metadata, try to read it from the local registry
        if not skill_name or not repo_url:
            agent = self._gateway._base_agent
            registry = getattr(agent, "_skill_registry", None)
            if registry and skill_name:
                meta = registry._skills.get(skill_name, {})
                skill_desc = skill_desc or meta.get("description", "")

        if not skill_name:
            await ws.send(json.dumps({
                "type": "publish_skill_url",
                "ok": False,
                "error": "skill_name is required",
            }))
            return

        body_lines = [
            f"**Skill name:** {skill_name}",
            f"**Description:** {skill_desc or '(add a description)'}",
            f"**Repository URL:** {repo_url or 'https://github.com/YOUR_USER/YOUR_REPO'}",
            "",
            "<!-- Please fill in all fields above, then submit this issue. -->",
            "<!-- A maintainer will review and add your skill to the index. -->",
        ]
        body = quote("\n".join(body_lines), safe="")
        name_encoded = quote(skill_name, safe="")
        url = self._PUBLISH_ISSUE_URL.format(name=name_encoded, body=body)

        await ws.send(json.dumps({
            "type": "publish_skill_url",
            "ok": True,
            "url": url,
            "skill_name": skill_name,
        }))

    # ── File upload via WebSocket ──────────────────────────────────────────

    async def _ws_handle_upload(self, ws, data: dict) -> None:
        """Handle {type: "file_upload"} WS message.

        The browser sends the file as base64 in the ``data`` field together
        with an optional ``upload_id`` for correlation.  We decode, validate,
        persist and respond with {type: "file_uploaded"}.

        This approach sidesteps websockets' HTTP parser which only supports
        GET requests, so a raw PUT endpoint cannot be used with websockets ≥ 13.
        """
        import base64
        import re
        from uuid import uuid4

        upload_id = data.get("upload_id", "")
        name      = data.get("name", "upload")
        b64       = data.get("data", "")

        async def _err(msg: str) -> None:
            await ws.send(json.dumps({
                "type": "file_uploaded", "ok": False,
                "error": msg, "upload_id": upload_id,
            }))

        if not b64:
            await _err("No data provided")
            return

        safe_name = re.sub(r"[^\w.\-]", "_", name)[:128] or "upload"
        try:
            file_bytes = base64.b64decode(b64)
        except Exception:
            await _err("Invalid base64 data")
            return

        max_bytes = self._config.max_upload_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            await _err(f"File too large (max {self._config.max_upload_mb} MB)")
            return

        file_id  = uuid4().hex[:12]
        filename = f"{file_id}_{safe_name}"
        (self._upload_dir / filename).write_bytes(file_bytes)
        log.info("Uploaded file (WS): %s (%d bytes)", filename, len(file_bytes))

        await ws.send(json.dumps({
            "type":      "file_uploaded",
            "ok":        True,
            "upload_id": upload_id,
            "file_id":   file_id,
            "name":      safe_name,
            "url":       f"/files/{filename}",
            "size":      len(file_bytes),
        }))

    # ── File upload / download ─────────────────────────────────────────────

    def _check_http_auth(self, request, query: str) -> bool:
        """Return True if the request satisfies API key auth (or no key is required)."""
        if not self._config.api_key:
            return True
        key = request.headers.get("X-API-Key", "")
        if key == self._config.api_key:
            return True
        from urllib.parse import parse_qs
        return parse_qs(query).get("api_key", [""])[0] == self._config.api_key

    async def _handle_upload(self, connection, request, query: str):
        """Handle PUT /upload?name=<filename> — store file, return JSON with file_id."""
        import re
        from urllib.parse import parse_qs
        from uuid import uuid4

        if not self._check_http_auth(request, query):
            body = b'{"ok":false,"error":"Unauthorized"}'
            return _make_response(HTTPStatus.UNAUTHORIZED, [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ], body)

        params = parse_qs(query)
        raw_name = params.get("name", ["upload"])[0]
        safe_name = re.sub(r"[^\w.\-]", "_", raw_name)[:128] or "upload"

        max_bytes = self._config.max_upload_mb * 1024 * 1024

        try:
            cl = int(request.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            cl = 0

        if cl > max_bytes:
            body = json.dumps({"ok": False, "error": f"File too large (max {self._config.max_upload_mb} MB)"}).encode()
            return _make_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ], body)

        # Read raw body bytes
        file_bytes = getattr(request, "body", None)
        if file_bytes is None:
            if cl > 0:
                try:
                    file_bytes = await asyncio.wait_for(
                        connection.reader.read(cl), timeout=60
                    )
                except asyncio.TimeoutError:
                    body = b'{"ok":false,"error":"Upload timeout"}'
                    return _make_response(HTTPStatus.REQUEST_TIMEOUT, [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                        ("Connection", "close"),
                    ], body)
            else:
                file_bytes = b""

        if len(file_bytes) > max_bytes:
            body = json.dumps({"ok": False, "error": f"File too large (max {self._config.max_upload_mb} MB)"}).encode()
            return _make_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ], body)

        file_id = uuid4().hex[:12]
        filename = f"{file_id}_{safe_name}"
        (self._upload_dir / filename).write_bytes(file_bytes)
        log.info("Uploaded file: %s (%d bytes)", filename, len(file_bytes))

        resp = json.dumps({
            "ok": True,
            "file_id": file_id,
            "name": safe_name,
            "url": f"/files/{filename}",
            "size": len(file_bytes),
        }).encode()
        return _make_response(HTTPStatus.OK, [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(resp))),
            ("Connection", "close"),
        ], resp)

    async def _serve_file(self, request, query: str, fid_path: str):
        """Handle GET /files/<file_id_filename> — serve uploaded file."""
        if not self._check_http_auth(request, query):
            return _make_response(HTTPStatus.UNAUTHORIZED, [
                ("Content-Type", "text/plain"),
                ("Connection", "close"),
            ], b"Unauthorized")

        if not fid_path:
            return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")

        # Exact match first
        target = self._upload_dir / fid_path
        if not target.exists() or not target.is_file():
            # Prefix match by file_id (first segment before _)
            file_id = fid_path.split("_")[0]
            matches = list(self._upload_dir.glob(f"{file_id}_*"))
            target = matches[0] if matches else None

        if not target or not target.exists() or not target.is_file():
            return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")

        file_bytes = target.read_bytes()
        mime = _MIME.get(target.suffix, "application/octet-stream")
        parts = target.name.split("_", 1)
        display_name = parts[1] if len(parts) > 1 else target.name

        return _make_response(HTTPStatus.OK, [
            ("Content-Type", mime),
            ("Content-Length", str(len(file_bytes))),
            ("Content-Disposition", f'attachment; filename="{display_name}"'),
            ("Connection", "close"),
        ], file_bytes)

    # ── Chat / pipeline handlers ───────────────────────────────────────────

    async def _handle_chat(self, ws, data: dict, session_ids: dict) -> None:
        agent = data.get("agent", "default")
        text = data.get("text", "").strip()

        # Inject attached file paths into message text so the agent can use read_file()
        attachments = data.get("attachments") or []
        if attachments:
            lines = [text] if text else []
            lines.append("\n[Attached files]")
            for att in attachments:
                name = att.get("name", "file")
                file_id = att.get("file_id", "")
                if file_id:
                    matches = list(self._upload_dir.glob(f"{file_id}_*"))
                    local_path = str(matches[0]) if matches else att.get("url", "")
                else:
                    local_path = att.get("url", "")
                lines.append(f"- {name} (local path: {local_path})")
            text = "\n".join(lines).strip()

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or session_ids.get(agent) or make_id("s-")
        session_ids[agent] = session_id
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))

        try:
            async for event in self._gateway.event_stream(agent, text, session_id):
                await ws.send(json.dumps(event))
        except Exception as e:
            log.error("event_stream error: %s", e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_pipeline(self, ws, data: dict, session_ids: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        agents_raw = data.get("agents", [])
        if isinstance(agents_raw, str):
            agent_names = self._gateway.resolve_pipeline(agents_raw)
        elif isinstance(agents_raw, list):
            agent_names = agents_raw
        else:
            await ws.send(json.dumps({"type": "error", "message": "agents must be a list or string"}))
            return

        if not agent_names:
            await ws.send(json.dumps({"type": "error", "message": "No agents specified for pipeline"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))

        try:
            async for event in self._gateway.pipeline_stream(agent_names, text, session_id):
                await ws.send(json.dumps(event))
        except Exception as e:
            log.error("pipeline_stream error: %s", e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_orchestrate(self, ws, data: dict, session_ids: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))

        try:
            result = await self._gateway.orchestrate(text, session_id)
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("orchestrate error: %s", e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_run_hierarchical(self, ws, data: dict, session_ids: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return
        commander = (data.get("commander") or "").strip()
        if not commander:
            await ws.send(json.dumps({"type": "error", "message": "commander is required"}))
            return
        mode = (data.get("mode") or "parallel").strip().lower()
        session_id = data.get("session_id") or session_ids.get(commander) or make_id("s-")
        session_ids[commander] = session_id
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        try:
            result = await self._gateway.execute_hierarchical(
                commander_name=commander,
                text=text,
                mode=mode,
                session_id=session_id,
            )
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("run_hierarchical error: %s", e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

