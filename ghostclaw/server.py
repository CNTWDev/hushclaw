"""GhostClaw WebSocket server — requires 'websockets>=12.0' (pip install ghostclaw[server])."""
from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from pathlib import Path


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
_MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


def _dict_to_toml(data: dict) -> str:
    """Minimal TOML serializer for flat-section config dicts (no arrays-of-tables)."""
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

    # Sections
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"\n[{k}]")
            for sk, sv in v.items():
                if isinstance(sv, list) and all(not isinstance(i, dict) for i in sv):
                    lines.append(f"{sk} = {_list_val(sv)}")
                elif not isinstance(sv, (dict, list)):
                    s = _scalar(sv)
                    if s is not None:
                        lines.append(f"{sk} = {s}")

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
            await asyncio.Future()  # run forever

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
            key = ""
            try:
                key = ws.request.headers.get("X-API-Key", "")
            except Exception:
                pass
            if key != self._config.api_key:
                await ws.close(1008, "Unauthorized")
                return

        remote = getattr(ws, "remote_address", "?")
        log.info("Client connected: %s", remote)

        session_ids: dict[str, str] = {}  # agent_name → session_id

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
                await self._dispatch(ws, data, session_ids)
        except Exception as e:
            log.debug("Client %s disconnected: %s", remote, e)
        finally:
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
        elif msg_type == "get_config_status":
            await ws.send(json.dumps(self._config_status()))
        elif msg_type == "save_config":
            await self._handle_save_config(ws, data)
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))

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

        return {
            "type": "config_status",
            "configured": (not needs_key) or bool(api_key),
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "system_prompt": cfg.agent.system_prompt,
            "config_file": cfg_file,
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
                    if v != "":          # skip empty strings (wizard left blank)
                        sec[k] = v

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
            log.info(
                "Config reloaded: provider=%s model=%s",
                new_cfg.provider.name, new_cfg.agent.model,
            )
        except Exception as exc:
            log.error("Config reload error: %s", exc, exc_info=True)

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

