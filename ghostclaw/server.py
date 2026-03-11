"""GhostClaw WebSocket server — requires 'websockets>=12.0' (pip install ghostclaw[server])."""
from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from pathlib import Path

from ghostclaw.config.schema import ServerConfig
from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger

log = get_logger("server")

_WEB_DIR = Path(__file__).parent / "web"
_MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


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
            import websockets
        except ImportError:
            raise ImportError(
                "websockets is required for 'ghostclaw serve'. "
                "Install with: pip install 'ghostclaw[server]'"
            ) from None

        log.info(
            "Starting GhostClaw server on %s:%d",
            self._config.host, self._config.port,
        )

        async with websockets.serve(
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
        """websockets process_request hook: serve static files for HTTP GET, pass through WS upgrades."""
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
            headers = [("Content-Type", mime), ("Content-Length", str(len(body)))]
            return connection.respond(HTTPStatus.OK, headers, body)
        return connection.respond(HTTPStatus.NOT_FOUND, [], b"Not found")

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
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))

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

