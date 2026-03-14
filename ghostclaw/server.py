"""GhostClaw WebSocket server — requires 'websockets>=12.0' (pip install ghostclaw[server])."""
from __future__ import annotations

import asyncio
import json
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

from ghostclaw.config.schema import ServerConfig
from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger

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


class GhostClawServer:
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

        from ghostclaw.scheduler import Scheduler
        memory = gateway._base_agent.memory
        self._scheduler = Scheduler(memory, gateway)
        # Inject scheduler into all agents so tools can reference it
        for pool in gateway._pools.values():
            pool._agent._scheduler = self._scheduler

        from ghostclaw.connectors.manager import ConnectorsManager
        self._connectors = ConnectorsManager(gateway._base_agent.config.connectors, gateway)

    async def start(self) -> None:
        try:
            from websockets.asyncio.server import serve as _ws_serve
        except ImportError:
            raise ImportError(
                "websockets>=12.0 is required for 'ghostclaw serve'. "
                "Install with: pip install 'ghostclaw[server]'"
            ) from None

        log.info(
            "Starting GhostClaw server on %s:%d",
            self._config.host, self._config.port,
        )

        async with _ws_serve(
            self._handle_client,
            self._config.host,
            self._config.port,
            max_size=4 * 1024 * 1024,  # 4MB
            process_request=self._http_handler,
        ):
            print(
                f"GhostClaw server listening on "
                f"http://{self._config.host}:{self._config.port}"
            )
            if self._config.api_key:
                print("API key authentication enabled (X-API-Key header).")
            await self._scheduler.start()
            await self._connectors.start()
            try:
                await asyncio.Future()  # run forever
            finally:
                await self._connectors.stop()
                await self._scheduler.stop()

    async def _http_handler(self, connection, request):
        """websockets asyncio process_request hook: serve static files, pass WS upgrades through."""
        try:
            if request.headers.get("upgrade", "").lower() == "websocket":
                return None  # let websockets handle WS upgrade normally
            path = request.path.split("?")[0]
            if path == "/":
                path = "/index.html"
            file_path = _WEB_DIR / path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                suffix = file_path.suffix
                mime = _MIME.get(suffix, "application/octet-stream")
                body = file_path.read_bytes()
                headers = [
                    ("Content-Type", mime),
                    ("Content-Length", str(len(body))),
                    ("Connection", "close"),
                ]
                return _make_response(HTTPStatus.OK, headers, body)
            return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")
        except Exception as exc:
            log.error("HTTP handler error: %s", exc, exc_info=True)
            try:
                return _make_response(HTTPStatus.INTERNAL_SERVER_ERROR, [("Connection", "close")], b"Server error")
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
                    from ghostclaw.util.ids import make_id
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
        elif msg_type == "orchestrate":
            await self._handle_orchestrate(ws, data, session_ids)
        elif msg_type == "list_agents":
            await ws.send(json.dumps({"type": "agents", "items": self._gateway.list_agents()}))
        elif msg_type == "list_sessions":
            items = self._gateway._base_agent.list_sessions()
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
        elif msg_type == "list_skills":
            await self._handle_list_skills(ws)
        elif msg_type == "list_skill_repos":
            await self._handle_list_skill_repos(ws)
        elif msg_type == "install_skill_repo":
            await self._handle_install_skill_repo(ws, data)
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

        from ghostclaw.config.loader import get_config_dir
        cfg_file = str(get_config_dir() / "ghostclaw.toml")

        tg = cfg.connectors.telegram
        fs = cfg.connectors.feishu
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
                    "enabled": tg.enabled,
                    "bot_token_set": bool(tg.bot_token),
                    "agent": tg.agent,
                    "allowlist": tg.allowlist,
                    "stream": tg.stream,
                },
                "feishu": {
                    "enabled": fs.enabled,
                    "app_id_set": bool(fs.app_id),
                    "app_secret_set": bool(fs.app_secret),
                    "agent": fs.agent,
                    "allowlist": fs.allowlist,
                    "stream": fs.stream,
                },
            },
            "browser": {
                "enabled":              cfg.browser.enabled,
                "headless":             cfg.browser.headless,
                "timeout":              cfg.browser.timeout,
                "playwright_installed": self._check_playwright(),
            },
        }

    async def _handle_save_config(self, ws, data: dict) -> None:
        """Write wizard-supplied config to the user config TOML file."""
        from ghostclaw.config.loader import get_config_dir, _load_toml

        incoming: dict = data.get("config", {})
        cfg_dir = get_config_dir()
        cfg_file = cfg_dir / "ghostclaw.toml"

        try:
            existing: dict = _load_toml(cfg_file)
        except Exception:
            existing = {}

        # Deep-merge only the sections the wizard touched
        for section in ("provider", "agent", "context", "server"):
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

        # Browser section
        if "browser" in incoming and isinstance(incoming["browser"], dict):
            br_sec = existing.setdefault("browser", {})
            for k, v in incoming["browser"].items():
                if isinstance(v, (bool, int)):
                    br_sec[k] = v
                elif isinstance(v, str) and v != "":
                    br_sec[k] = v

        # Connectors section has one extra level of nesting (connectors.telegram / connectors.feishu)
        if "connectors" in incoming and isinstance(incoming["connectors"], dict):
            conn_sec = existing.setdefault("connectors", {})
            for platform in ("telegram", "feishu"):
                plat_in = incoming["connectors"].get(platform)
                if not isinstance(plat_in, dict):
                    continue
                plat_sec = conn_sec.setdefault(platform, {})
                for k, v in plat_in.items():
                    if isinstance(v, str):
                        v = v.strip()
                    # booleans and lists always overwrite; empty strings are skipped
                    if isinstance(v, (bool, list)):
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
            from ghostclaw.config.loader import load_config
            from ghostclaw.providers.registry import get_provider
            new_cfg = load_config()
            agent = self._gateway._base_agent
            agent.config = new_cfg
            agent.provider = get_provider(new_cfg.provider)
            # Rebuild tool registry so browser_enabled changes take effect immediately
            agent.registry._tools = {}
            agent.registry.load_builtins(
                enabled=new_cfg.tools.enabled,
                browser_enabled=new_cfg.browser.enabled,
            )
            if new_cfg.tools.plugin_dir:
                agent.registry.load_plugins(new_cfg.tools.plugin_dir)
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
        from ghostclaw.config.schema import ProviderConfig
        from ghostclaw.providers.registry import get_provider
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
        from ghostclaw.config.schema import ProviderConfig
        from ghostclaw.providers.registry import get_provider
        from ghostclaw.providers.base import Message
        base_cfg = self._gateway._base_agent.config.provider
        # Use a short timeout — this is a connectivity check, not a real request
        cfg = ProviderConfig(
            name=data.get("provider") or base_cfg.name,
            api_key=(data.get("api_key") or base_cfg.api_key or "").strip(),
            base_url=(data.get("base_url") or base_cfg.base_url or "").strip(),
            timeout=10,
            max_retries=0,
        )
        model = (data.get("model") or self._gateway._base_agent.config.agent.model or "").strip()

        async def _do_test() -> tuple[bool, str]:
            provider = get_provider(cfg)
            # Try list_models first — cheap, no token cost
            models = await provider.list_models()
            if models:
                return True, f"Connected. {len(models)} model(s) available."
            # list_models returned empty (some providers don't implement it) —
            # fall back to a minimal chat completion
            await provider.complete(
                messages=[Message(role="user", content="ping")],
                system="",
                max_tokens=1,
                model=model or None,
            )
            return True, "Connection successful."

        try:
            ok, detail = await asyncio.wait_for(_do_test(), timeout=15.0)
            await ws.send(json.dumps({
                "type": "test_provider_result",
                "ok": ok,
                "detail": detail,
            }))
        except asyncio.TimeoutError:
            await ws.send(json.dumps({
                "type": "test_provider_result",
                "ok": False,
                "detail": "Connection timed out (15 s). Check your endpoint and API key.",
            }))
        except Exception as e:
            await ws.send(json.dumps({
                "type": "test_provider_result",
                "ok": False,
                "detail": str(e),
            }))

    async def _handle_list_skills(self, ws) -> None:
        agent = self._gateway._base_agent
        registry = getattr(agent, "_skill_registry", None)
        items = registry.list_all() if registry else []
        skill_dir = str(agent.config.tools.skill_dir or "")
        await ws.send(json.dumps({
            "type": "skills",
            "items": items,
            "skill_dir": skill_dir,
            "configured": bool(skill_dir),
        }))

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

    async def _handle_list_skill_repos(self, ws) -> None:
        import time
        import urllib.request
        from ghostclaw.util.ssl_context import make_ssl_context

        now = time.time()
        if self._skill_repo_cache is None or now - self._skill_repo_cache_time >= 300:
            github_repos: list = []
            error_msg = ""
            try:
                # Search GitHub for skill repos tagged with ghostclaw/openclaw
                api_url = (
                    "https://api.github.com/search/repositories"
                    "?q=ghostclaw+skill+in:name,description,topics"
                    "&sort=stars&order=desc&per_page=6"
                )
                req = urllib.request.Request(
                    api_url,
                    headers={
                        "User-Agent": "GhostClaw/1.0",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                loop = asyncio.get_event_loop()

                def _fetch():
                    return urllib.request.urlopen(
                        req, timeout=8, context=make_ssl_context()
                    ).read()

                raw = await loop.run_in_executor(None, _fetch)
                data_gh = json.loads(raw)
                # Filter out repos that are clearly not skill packs
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

            # Always keep curated repos; supplement with GitHub results
            self._skill_repo_cache = list(self._CURATED_REPOS) + github_repos
            self._skill_repo_cache_time = now
            if error_msg and not github_repos:
                log.debug("Showing curated repos only (GitHub search failed: %s)", error_msg)

        # Shallow-copy items so we can add 'installed' without mutating the cache
        skill_dir = self._gateway._base_agent.config.tools.skill_dir
        repos_out = []
        for r in self._skill_repo_cache:
            item = dict(r)
            if skill_dir:
                repo_name = r["url"].rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
                item["installed"] = (skill_dir / repo_name).exists()
            else:
                item["installed"] = False
            repos_out.append(item)

        await ws.send(json.dumps({"type": "skill_repos", "items": repos_out}))

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
        skill_dir = agent.config.tools.skill_dir
        if not skill_dir:
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "skill_dir is not configured. Add [tools] skill_dir = \"~/.ghostclaw/skills\" to ghostclaw.toml.",
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
            from ghostclaw.skills.loader import SkillRegistry
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

            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": True,
                "url": url,
                "repo": repo_name,
                "skill_count": len(agent._skill_registry),
                "repo_skill_count": repo_skill_count,
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

    async def _handle_chat(self, ws, data: dict, session_ids: dict) -> None:
        agent = data.get("agent", "default")
        text = data.get("text", "").strip()
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

