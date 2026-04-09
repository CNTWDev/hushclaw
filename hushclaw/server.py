"""HushClaw WebSocket server — requires 'websockets>=12.0' (pip install hushclaw[server])."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field as _dc_field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Session-level task / subscriber decoupling ────────────────────────────────

_BUFFER_LIMIT = 400   # max events buffered per live session
_SESSION_TTL  = 1800  # seconds to retain a finished session entry (30 min)

# Only buffer events that are meaningful for reconnect replay
_REPLAY_EVENTS = frozenset({
    "session", "tool_call", "tool_result", "done", "error",
    "round_info", "session_status", "compaction", "pipeline_step",
})


@dataclass
class _SessionEntry:
    """Server-level session state that outlives any single WebSocket connection."""

    session_id: str
    task: object = None            # asyncio.Task | None
    buffer: deque = _dc_field(default_factory=lambda: deque(maxlen=_BUFFER_LIMIT))
    text: str = ""                 # accumulated response text from streaming chunks
    subscriber: object = None      # current WebSocket | None
    created_at: float = _dc_field(default_factory=time.time)
    finished_at: float | None = None

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()


class _SessionSink:
    """
    Duck-typed WebSocket proxy passed to streaming handlers in place of a real ws.

    • Buffers replay-worthy events into the SessionEntry.
    • Accumulates ``chunk`` text into SessionEntry.text.
    • Forwards every message to the current subscriber when one is attached.
    Subscriber failures are swallowed and the subscriber is cleared.
    """

    __slots__ = ("_entry",)

    def __init__(self, entry: _SessionEntry) -> None:
        self._entry = entry

    async def send(self, raw: str) -> None:
        try:
            evt = json.loads(raw)
            t = evt.get("type", "")
            if t == "chunk":
                self._entry.text += evt.get("text", "")
            elif t in _REPLAY_EVENTS:
                self._entry.buffer.append(raw)
        except Exception:
            pass

        sub = self._entry.subscriber
        if sub is not None:
            try:
                await sub.send(raw)
            except Exception:
                self._entry.subscriber = None

    @property
    def remote_address(self):
        sub = self._entry.subscriber
        return getattr(sub, "remote_address", "background") if sub else "background"


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
from hushclaw.update import UpdateExecutor, UpdateService

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
        # Session-local pending prompt-only skill command context.
        # Key: session_id, value: {"skill": str, "description": str}
        self._pending_skill_prompts: dict[str, dict[str, str]] = {}
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
        # Update subsystem
        self._update_service = UpdateService(
            cache_ttl_seconds=max(60, int(getattr(gateway.base_agent.config.update, "cache_ttl_seconds", 900))),
        )
        self._update_executor = UpdateExecutor()
        self._running_sessions: set[str] = set()
        # Server-level session registry: tasks survive individual WS connections
        self._session_tasks: dict[str, _SessionEntry] = {}

        # File upload directory (resolved from config or data_dir/uploads)
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

    @staticmethod
    def _normalize_note_payload(item: dict) -> dict:
        """Normalize memory rows for WebUI rendering."""
        out = dict(item or {})
        created = out.get("created")
        modified = out.get("modified")
        out["created_at"] = int(created or modified or 0) if (created or modified) else 0
        if modified is not None:
            out["updated_at"] = int(modified)
        return out

    @staticmethod
    def _is_auto_extract_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_extract" in tags

    @staticmethod
    def _is_system_note(item: dict) -> bool:
        """True for internal system notes that should never appear in the user-facing memory list."""
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return bool({"_compact_archive", "_compact_abstractive"} & set(tags))

    @staticmethod
    def _is_compacted_auto_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_compact" in tags

    @staticmethod
    def _clean_auto_body(text: str) -> str:
        s = " ".join((text or "").split()).strip()
        s = re.sub(r"\*+", "", s)
        s = s.strip(" \t\r\n。，、,.;；:：\"'（）()[]【】「」『』-*_")
        return s

    @classmethod
    def _is_low_value_auto_note(cls, item: dict) -> bool:
        body = cls._clean_auto_body(item.get("body", ""))
        if not body:
            return True
        lower = body.lower()
        if any(p in body or p in lower for p in (
            "保存到记忆", "并保存到记忆", "已保存到记忆", "save to memory", "saved to memory",
        )):
            return True
        if len(body) < 8:
            return True
        if body.startswith(("并", "以及", "并且", "另外", "然后", "且", "and ", "then ")):
            return True
        if body.endswith((",", "，", ";", "；", ":", "：", '"', "'")):
            return True
        substantive = re.findall(r"[\w\u4e00-\u9fff]", body)
        if len(substantive) < 4:
            return True
        if len(substantive) / max(len(body), 1) < 0.45:
            return True
        return False

    def _compact_auto_memories(self, *, group_limit: int = 24) -> dict:
        """One-click cleanup + compression for auto-extracted memories."""
        mem = self._gateway.memory
        rows = mem.conn.execute(
            "SELECT n.note_id, n.title, n.tags, n.created, b.body "
            "FROM notes n LEFT JOIN note_bodies b USING(note_id) "
            "ORDER BY n.created DESC"
        ).fetchall()
        notes = []
        for r in rows:
            tags_raw = r["tags"] or "[]"
            try:
                tags = json.loads(tags_raw)
            except Exception:
                tags = []
            notes.append({
                "note_id": r["note_id"],
                "title": r["title"] or "",
                "tags": tags,
                "created": int(r["created"] or 0),
                "body": r["body"] or "",
            })

        auto_notes = [n for n in notes if self._is_auto_extract_note(n)]
        junk = [n for n in auto_notes if (not self._is_compacted_auto_note(n)) and self._is_low_value_auto_note(n)]

        deleted_junk = 0
        for n in junk:
            if mem.delete_note(n["note_id"]):
                deleted_junk += 1

        # Rebuild candidate list after junk deletion; keep compact notes as-is.
        keep_auto = [
            n for n in auto_notes
            if n["note_id"] not in {x["note_id"] for x in junk} and not self._is_compacted_auto_note(n)
        ]
        by_day: dict[str, list[dict]] = {}
        for n in keep_auto:
            day = time.strftime("%Y-%m-%d", time.localtime(n["created"] or int(time.time())))
            by_day.setdefault(day, []).append(n)

        compressed_groups = 0
        compressed_sources = 0
        for day, items in by_day.items():
            if len(items) < 3:
                continue
            uniq: list[str] = []
            seen_lines: set[str] = set()
            for it in sorted(items, key=lambda x: x["created"]):
                line = self._clean_auto_body(it.get("body", ""))
                if not line or line in seen_lines:
                    continue
                seen_lines.add(line)
                uniq.append(line)
                if len(uniq) >= group_limit:
                    break
            if len(uniq) < 3:
                continue
            title = f"Auto Summary {day}"
            content = "\n".join(f"- {x}" for x in uniq)
            mem.remember(
                content,
                title=title,
                tags=["_auto_extract", "_auto_compact"],
            )
            compressed_groups += 1
            for it in items:
                if mem.delete_note(it["note_id"]):
                    compressed_sources += 1

        return {
            "deleted_junk": deleted_junk,
            "compressed_groups": compressed_groups,
            "compressed_sources": compressed_sources,
            "auto_total_before": len(auto_notes),
        }

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
            await self._scheduler.start()
            await self._connectors.start()
            await self._start_config_watcher()
            try:
                async with api_server:
                    await asyncio.Future()  # run forever
            finally:
                if self._config_watcher_task:
                    self._config_watcher_task.cancel()
                await self._connectors.stop()
                await self._scheduler.stop()

    # ── HTTP API server (POST proxy for community / auth APIs) ────────────
    # websockets 16 rejects non-GET methods before process_request is called.
    # This minimal asyncio stream server runs on port+1 and handles POST/OPTIONS.

    _COMMUNITY_BASE = "https://bus-ie.aibotplatform.com/hushclaw/community/api/v1/community"
    _AUTH_BASE       = "https://bus-ie.aibotplatform.com/assistant/vendor-api/v1/auth"

    async def _http_api_handler(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one HTTP connection on the API port (port+1)."""
        import urllib.request as _urlreq
        import urllib.error   as _urlerr
        from hushclaw.util.ssl_context import make_ssl_context

        def _write(status: int, body: bytes, extra_headers: list | None = None) -> None:
            try:
                phrase = HTTPStatus(status).phrase
            except ValueError:
                phrase = "Unknown"
            hdrs = [
                f"HTTP/1.1 {status} {phrase}",
                "Content-Type: application/json; charset=utf-8",
                f"Content-Length: {len(body)}",
                "Connection: close",
                "Access-Control-Allow-Origin: *",
                "Access-Control-Allow-Methods: POST, OPTIONS",
                "Access-Control-Allow-Headers: Content-Type, Authorization",
            ]
            if extra_headers:
                hdrs.extend(extra_headers)
            writer.write(("\r\n".join(hdrs) + "\r\n\r\n").encode() + body)

        def _do_post(target: str, req_body: bytes, req_auth: str) -> tuple[int, bytes]:
            """Blocking HTTP POST — called via asyncio.to_thread."""
            post_hdrs: dict[str, str] = {"Content-Type": "application/json"}
            if req_auth:
                post_hdrs["Authorization"] = req_auth
            req = _urlreq.Request(target, data=req_body, headers=post_hdrs, method="POST")
            try:
                with _urlreq.urlopen(req, context=make_ssl_context(), timeout=30) as r:
                    return r.status, r.read()
            except _urlerr.HTTPError as exc:
                return exc.code, (exc.read() or b"{}")

        try:
            # --- Read request line ---
            req_line = await asyncio.wait_for(reader.readline(), timeout=5)
            req_line = req_line.decode("utf-8", errors="replace").strip()
            if not req_line:
                return
            parts = req_line.split(" ", 2)
            if len(parts) < 2:
                return
            method, path = parts[0].upper(), parts[1]

            # --- Read headers ---
            hdrs: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                line = line.decode("utf-8", errors="replace")
                if line in ("\r\n", "\n", ""):
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.lower().strip()] = v.strip()

            # --- CORS preflight ---
            if method == "OPTIONS":
                _write(204, b"")
                await writer.drain()
                return

            if method != "POST":
                _write(405, b'{"error":"method not allowed"}')
                await writer.drain()
                return

            # --- Read body ---
            cl = int(hdrs.get("content-length", 0))
            body = await asyncio.wait_for(reader.readexactly(cl), timeout=10) if cl > 0 else b""
            auth = hdrs.get("authorization", "")

            # --- Route ---
            if path.startswith("/api/community/"):
                # path[15:] strips "/api/community" (14 chars) + the trailing slash (1),
                # giving "board/list" — then prepend "/" so target ends up correct.
                api_path = "/" + path[15:]  # e.g. /api/community/board/list → /board/list
                target   = self._COMMUNITY_BASE + api_path

                # Community API requires the gRPC-gateway envelope:
                # {"metadata": {...}, "payload": <actual_params>}
                # The browser sends only the inner payload, so we wrap it here.
                import time as _time, hashlib as _hl, random as _rnd
                import string as _str, datetime as _dt
                payload_data = json.loads(body.decode("utf-8", errors="replace")) if body else {}
                _meta = {
                    "appID":     "hushclaw",
                    "requestID": _hl.sha256(
                        f"{_time.time()}"
                        f"{''.join(_rnd.choices(_str.ascii_lowercase, k=8))}".encode()
                    ).hexdigest()[:32],
                    "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
                }
                wrapped = json.dumps({"metadata": _meta, "payload": payload_data}).encode()
                status, resp_body = await asyncio.to_thread(_do_post, target, wrapped, auth)
                _write(status, resp_body)
                await writer.drain()

            elif path == "/api/auth/send-email-code":
                # Use existing Python logic (handles auth API details)
                from hushclaw.providers.transsion import send_email_code
                req_data = json.loads(body.decode()) if body else {}
                email = (req_data.get("email") or "").strip()
                if not email:
                    _write(400, b'{"error":"email is required"}')
                    await writer.drain()
                    return
                try:
                    await asyncio.to_thread(send_email_code, email)
                    _write(200, json.dumps({"ok": True, "email": email}).encode())
                except Exception as exc:
                    _write(502, json.dumps({"error": str(exc)}).encode())
                await writer.drain()

            elif path == "/api/auth/login":
                import functools
                from hushclaw.providers.transsion import acquire_credentials
                req_data = json.loads(body.decode()) if body else {}
                email = (req_data.get("email") or "").strip()
                code  = (req_data.get("code")  or "").strip()
                if not email or not code:
                    _write(400, b'{"error":"email and code are required"}')
                    await writer.drain()
                    return
                try:
                    creds = await asyncio.to_thread(
                        functools.partial(acquire_credentials, email, code)
                    )
                    base_url_v1 = creds["base_url"].rstrip("/") + "/v1"
                    result = {
                        "display_name":  creds["display_name"],
                        "email":         creds["email"],
                        "access_token":  creds["access_token"],
                        "api_key":       creds["api_key"],
                        "models":        creds["models"],
                        "quota_remain":  creds["quota_remain"],
                        "base_url":      base_url_v1,
                    }
                    _write(200, json.dumps(result).encode())
                except Exception as exc:
                    _write(502, json.dumps({"error": str(exc)}).encode())
                await writer.drain()

            else:
                _write(404, b'{"error":"not found"}')
                await writer.drain()
                return

        except Exception as exc:
            log.debug("http_api_handler error: %s", exc)
            try:
                _write(500, json.dumps({"error": str(exc)}).encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

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
                # Use keep-alive for JS/CSS so Safari can reuse the TCP connection
                # when loading ES modules in parallel (avoids repeated TCP handshakes).
                conn_header = "close" if suffix == ".html" else "keep-alive"
                return _make_response(HTTPStatus.OK, [
                    ("Content-Type",   mime),
                    ("Cache-Control",  cache_control),
                    ("Content-Length", str(len(body))),
                    ("Connection",     conn_header),
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
                    from hushclaw.util.ids import make_id
                    sid = data.get("session_id") or session_ids.get(agent) or make_id("s-")
                    if not data.get("session_id"):
                        data = dict(data)
                        data["session_id"] = sid

                    entry = self._get_or_create_session_entry(sid)
                    entry.subscriber = ws
                    sink = _SessionSink(entry)

                    task = asyncio.create_task(self._dispatch(sink, data, session_ids))
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
                    await self._dispatch(ws, data, session_ids)

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

    async def _dispatch(self, ws, data: dict, session_ids: dict) -> None:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type == "chat":
            await self._handle_chat(ws, data, session_ids)
        elif msg_type == "broadcast_mention":
            await self._handle_broadcast_mention(ws, data, session_ids)
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
            gw_cfg = self._gateway.base_agent.config.gateway
            limit = int(data.get("limit", gw_cfg.session_list_limit))
            include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
            max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
            items = self._gateway.memory.list_sessions(
                limit=max(1, limit),
                include_scheduled=bool(include_scheduled),
                max_idle_days=max(0, max_idle_days),
            )
            await ws.send(json.dumps({"type": "sessions", "items": items}, default=str))
        elif msg_type == "list_memories":
            query = data.get("query", "")
            limit = int(data.get("limit", 20))
            include_auto = bool(data.get("include_auto", False))
            request_id = data.get("request_id")
            agent = self._gateway.base_agent
            items = agent.search(query, limit=limit) if query else agent.list_memories(limit=limit)
            # Always hide internal system notes (_compact_archive, _compact_abstractive)
            items = [m for m in items if not self._is_system_note(m)]
            if not include_auto:
                items = [m for m in items if not self._is_auto_extract_note(m)]
            items = [self._normalize_note_payload(m) for m in items]
            payload = {"type": "memories", "items": items}
            if request_id is not None:
                payload["request_id"] = request_id
            await ws.send(json.dumps(payload, default=str))
        elif msg_type == "delete_memory":
            raw = data.get("note_id")
            note_id = str(raw).strip() if raw is not None else ""
            ok = self._gateway.base_agent.forget(note_id) if note_id else False
            await ws.send(json.dumps({"type": "memory_deleted", "note_id": note_id, "ok": ok}))
        elif msg_type == "compact_memories":
            try:
                stats = self._compact_auto_memories()
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
        elif msg_type == "delete_session":
            sid = data.get("session_id", "")
            ok = self._gateway.memory.delete_session(sid) if sid else False
            await ws.send(json.dumps({"type": "session_deleted", "session_id": sid, "ok": ok}))
        elif msg_type == "get_session_history":
            sid = data.get("session_id", "")
            turns = self._gateway.memory.load_session_turns(sid)
            await ws.send(json.dumps({"type": "session_history", "session_id": sid, "turns": turns}, default=str))
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
        elif msg_type == "list_skill_repos":
            await self._handle_list_skill_repos(ws)
        elif msg_type == "install_skill_repo":
            await self._handle_install_skill_repo(ws, data)
        elif msg_type == "install_skill_zip":
            await self._handle_install_skill_zip(ws, data)
        elif msg_type == "publish_skill":
            await self._handle_publish_skill(ws, data)
        elif msg_type == "transsion_send_code":
            await self._handle_transsion_send_code(ws, data)
        elif msg_type == "transsion_login":
            await self._handle_transsion_login(ws, data)
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg_type!r}"}))

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright.async_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _config_status(self) -> dict:
        """Return current configuration state for the setup wizard."""
        cfg = self._gateway.base_agent.config
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
        upd = cfg.update
        last_update = self._update_service.last_result or {}
        return {
            "type": "config_status",
            "configured": (not needs_key) or bool(api_key),
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "public_base_url": cfg.server.public_base_url or "",
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "max_tool_rounds": cfg.agent.max_tool_rounds,
            "system_prompt": cfg.agent.system_prompt,
            "cost_per_1k_input_tokens": cfg.provider.cost_per_1k_input_tokens,
            "cost_per_1k_output_tokens": cfg.provider.cost_per_1k_output_tokens,
            "config_file": cfg_file,
            "update": {
                "auto_check_enabled": upd.auto_check_enabled,
                "check_interval_hours": upd.check_interval_hours,
                "channel": upd.channel,
                "last_checked_at": upd.last_checked_at or self._update_service.last_checked_at,
                "check_timeout_seconds": upd.check_timeout_seconds,
                "cache_ttl_seconds": upd.cache_ttl_seconds,
                "upgrade_timeout_seconds": upd.upgrade_timeout_seconds,
                "current_version": self._update_service.current_version,
                "latest_version": last_update.get("latest_version", ""),
                "update_available": bool(last_update.get("update_available", False)),
                "release_url": last_update.get("release_url", ""),
            },
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
                    "markdown":        tg.markdown,
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
                    "markdown":               fs.markdown,
                },
                "discord": {
                    "enabled":         dc.enabled,
                    "bot_token_set":   bool(dc.bot_token),
                    "agent":           dc.agent,
                    "allowlist":       dc.allowlist,
                    "guild_allowlist": dc.guild_allowlist,
                    "require_mention": dc.require_mention,
                    "stream":          dc.stream,
                    "markdown":        dc.markdown,
                },
                "slack": {
                    "enabled":       sl.enabled,
                    "bot_token_set": bool(sl.bot_token),
                    "app_token_set": bool(sl.app_token),
                    "agent":         sl.agent,
                    "allowlist":     sl.allowlist,
                    "stream":        sl.stream,
                    "markdown":      sl.markdown,
                },
                "dingtalk": {
                    "enabled":           dt.enabled,
                    "client_id":         dt.client_id,
                    "client_secret_set": bool(dt.client_secret),
                    "agent":             dt.agent,
                    "allowlist":         dt.allowlist,
                    "stream":            dt.stream,
                    "markdown":          dt.markdown,
                },
                "wecom": {
                    "enabled":          wc.enabled,
                    "corp_id":          wc.corp_id,
                    "corp_secret_set":  bool(wc.corp_secret),
                    "agent_id":         wc.agent_id,
                    "token_set":        bool(wc.token),
                    "agent":            wc.agent,
                    "allowlist":        wc.allowlist,
                    "markdown":         wc.markdown,
                },
            },
            "browser": {
                "enabled":                cfg.browser.enabled,
                "headless":               cfg.browser.headless,
                "timeout":                cfg.browser.timeout,
                "playwright_installed":   self._check_playwright(),
                "use_user_chrome":        bool(cfg.browser.remote_debugging_url),
                "remote_debugging_url":   cfg.browser.remote_debugging_url,
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
            "transsion": {
                "email":         cfg.transsion.email,
                "display_name":  cfg.transsion.display_name,
                "access_token":  cfg.transsion.access_token,
                "authed":        bool(cfg.transsion.email and cfg.provider.api_key
                                      and cfg.provider.name == "transsion"),
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
            "workspace_dir":  str(cfg.agent.workspace_dir or ""),
            "workspace": self._workspace_status(cfg),
            # Free-form API keys for skills/integrations.
            # Values are masked: only set/unset is exposed (never raw keys).
            "api_keys": {
                k: bool(v) for k, v in (cfg.api_keys or {}).items()
            },
            # The actual values are sent separately for the UI to pre-fill forms.
            # Sensitive — only sent to the settings wizard, not broadcast.
            "_api_keys_raw": dict(cfg.api_keys or {}),
        }

    def _workspace_status(self, cfg) -> dict:
        """Return workspace directory status for the setup wizard."""
        from pathlib import Path as _Path
        ws = cfg.agent.workspace_dir
        if ws is None:
            return {"configured": False, "path": "", "soul_md": False, "user_md": False}
        ws = _Path(ws)
        return {
            "configured": ws.is_dir(),
            "path": str(ws),
            "soul_md": (ws / "SOUL.md").exists(),
            "user_md": (ws / "USER.md").exists(),
        }

    async def _handle_init_workspace(self, ws, data: dict) -> None:
        """Create workspace directory and seed default SOUL.md/USER.md."""
        from pathlib import Path as _Path
        from hushclaw.config.loader import _bootstrap_workspace

        custom_path = (data.get("path") or "").strip()
        cfg = self._gateway.base_agent.config

        if custom_path:
            ws_dir = _Path(custom_path).expanduser()
        elif cfg.agent.workspace_dir:
            ws_dir = _Path(cfg.agent.workspace_dir)
        else:
            from hushclaw.config.loader import _data_dir
            ws_dir = _data_dir() / "workspace"

        try:
            _bootstrap_workspace(ws_dir)
            await ws.send(json.dumps({
                "type": "workspace_initialized",
                "ok": True,
                "path": str(ws_dir),
                "soul_md": (ws_dir / "SOUL.md").exists(),
                "user_md": (ws_dir / "USER.md").exists(),
            }))
        except Exception as exc:
            await ws.send(json.dumps({
                "type": "workspace_initialized",
                "ok": False,
                "error": str(exc),
            }))

    async def _handle_save_config(self, ws, data: dict) -> None:
        """Write wizard-supplied config to the user config TOML file."""
        import time
        from hushclaw.config.loader import get_config_dir, _load_toml

        t0 = time.perf_counter()
        save_cid = data.get("save_client_id")
        incoming: dict = data.get("config", {}) or {}
        prov_in = incoming.get("provider") if isinstance(incoming.get("provider"), dict) else {}
        api_key_len = len((prov_in.get("api_key") or "").strip()) if isinstance(prov_in, dict) else 0
        log.info(
            "save_config: begin save_client_id=%r sections=%s provider=%s api_key_len=%d transsion=%s",
            save_cid,
            list(incoming.keys()),
            (prov_in.get("name") if isinstance(prov_in, dict) else None),
            api_key_len,
            bool(incoming.get("transsion")),
        )

        cfg_dir = get_config_dir()
        cfg_file = cfg_dir / "hushclaw.toml"

        try:
            existing: dict = _load_toml(cfg_file)
        except Exception:
            existing = {}
        log.debug(
            "save_config: loaded existing keys save_client_id=%r ms=%.1f",
            save_cid,
            (time.perf_counter() - t0) * 1000,
        )

        # Deep-merge only the sections the wizard touched
        for section in ("provider", "agent", "context", "server", "update", "email", "calendar", "transsion"):
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

        # Agent section: workspace_dir (save separately to allow clearing)
        if "agent" in incoming and isinstance(incoming["agent"], dict):
            agent_in = incoming["agent"]
            if "workspace_dir" in agent_in:
                existing.setdefault("agent", {})["workspace_dir"] = (
                    agent_in["workspace_dir"].strip() if isinstance(agent_in["workspace_dir"], str)
                    else agent_in["workspace_dir"]
                )

        # Tools section (user_skill_dir)
        if "tools" in incoming and isinstance(incoming["tools"], dict):
            tools_sec = existing.setdefault("tools", {})
            for k, v in incoming["tools"].items():
                if isinstance(v, str):
                    v = v.strip()
                tools_sec[k] = v  # allow empty string to clear user_skill_dir

        # Browser section
        if "browser" in incoming and isinstance(incoming["browser"], dict):
            br_in  = incoming["browser"]
            br_sec = existing.setdefault("browser", {})
            for k, v in br_in.items():
                if k in ("use_user_chrome",):
                    # Virtual toggle — not stored; drives remote_debugging_url instead.
                    continue
                if isinstance(v, (bool, int)):
                    br_sec[k] = v
                elif isinstance(v, str) and v != "":
                    br_sec[k] = v
            # If "Use My Chrome" toggle was explicitly turned off, clear the URL.
            if br_in.get("use_user_chrome") is False:
                br_sec["remote_debugging_url"] = ""

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

        # api_keys — free-form dict; empty string values clear an existing key
        if "api_keys" in incoming and isinstance(incoming["api_keys"], dict):
            keys_sec = existing.setdefault("api_keys", {})
            for k, v in incoming["api_keys"].items():
                k = k.strip()
                if not k:
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    if v == "":
                        # Explicit empty string = clear the key
                        keys_sec.pop(k, None)
                    else:
                        keys_sec[k] = v
                elif v is not None:
                    keys_sec[k] = v

        def _ack_payload(ok: bool, **extra) -> dict:
            out = {
                "type": "config_saved",
                "ok": ok,
                "config_file": str(cfg_file),
                "restart_required": False,
                "save_client_id": save_cid,
            }
            out.update(extra)
            return out

        try:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            t_write = time.perf_counter()
            toml_text = _dict_to_toml(existing)
            cfg_file.write_text(toml_text, encoding="utf-8")
            log.info(
                "save_config: wrote file save_client_id=%r toml_chars=%d write_ms=%.1f total_ms=%.1f",
                save_cid,
                len(toml_text),
                (time.perf_counter() - t_write) * 1000,
                (time.perf_counter() - t0) * 1000,
            )
            # Ack immediately — _apply_config() can take 15s+ on large skill/plugin trees
            # and would make the wizard hit its client-side save timeout.
            t_send = time.perf_counter()
            await ws.send(json.dumps(_ack_payload(True)))
            log.info(
                "save_config: config_saved sent save_client_id=%r send_ms=%.1f total_ms=%.1f",
                save_cid,
                (time.perf_counter() - t_send) * 1000,
                (time.perf_counter() - t0) * 1000,
            )
            try:
                t_apply = time.perf_counter()
                self._apply_config()
                log.info(
                    "save_config: _apply_config ok save_client_id=%r apply_ms=%.1f",
                    save_cid,
                    (time.perf_counter() - t_apply) * 1000,
                )
            except Exception as apply_exc:
                log.error(
                    "save_config: file written but reload failed save_client_id=%r: %s",
                    save_cid,
                    apply_exc,
                    exc_info=True,
                )
        except Exception as e:
            log.error("save_config error save_client_id=%r: %s", save_cid, e, exc_info=True)
            try:
                await ws.send(json.dumps(_ack_payload(False, error=str(e))))
            except Exception as send_exc:
                log.error("save_config: failed to send error ack: %s", send_exc)

    async def _handle_save_update_policy(self, ws, data: dict) -> None:
        """Persist update policy settings from dedicated UI controls."""
        incoming = data.get("config", {}) or {}
        await self._handle_save_config(ws, {"config": {"update": incoming}})

    # ── Transsion / TEX AI Router auth flow ───────────────────────────────────

    async def _handle_transsion_send_code(self, ws, data: dict) -> None:
        """Send OTP verification code to the user's email (step 1 of Transsion auth)."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from hushclaw.providers.transsion import send_email_code

        email = (data.get("email") or "").strip()
        if not email:
            await ws.send(json.dumps({"type": "error", "message": "email is required"}))
            return

        log.info("transsion_send_code: requesting OTP for email=%s", email)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="hushclaw-transsion"),
                send_email_code,
                email,
            )
            log.info("transsion_send_code: OTP dispatched for email=%s", email)
            await ws.send(json.dumps({
                "type": "transsion_code_sent",
                "email": email,
            }))
        except Exception as e:
            log.exception("transsion_send_code failed for email=%s", email)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_transsion_login(self, ws, data: dict) -> None:
        """Log in with email + OTP code, acquire API credentials, and persist them (step 2)."""
        import asyncio
        import functools
        from concurrent.futures import ThreadPoolExecutor
        from hushclaw.providers.transsion import acquire_credentials

        email = (data.get("email") or "").strip()
        code = (data.get("code") or "").strip()
        if not email or not code:
            await ws.send(json.dumps({"type": "error", "message": "email and code are required"}))
            return

        log.info("transsion_login: starting acquire_credentials for email=%s", email)
        loop = asyncio.get_event_loop()
        try:
            creds: dict = await loop.run_in_executor(
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="hushclaw-transsion"),
                functools.partial(acquire_credentials, email, code),
            )
        except Exception as e:
            log.exception("transsion_login failed for email=%s", email)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))
            return

        # Do not write TOML here — user picks a model and clicks Save in the wizard.
        base_url_v1 = creds["base_url"].rstrip("/") + "/v1"
        await ws.send(json.dumps({
            "type": "transsion_authed",
            "display_name": creds["display_name"],
            "email": creds["email"],
            "access_token": creds["access_token"],
            "api_key": creds["api_key"],
            "models": creds["models"],
            "quota_remain": creds["quota_remain"],
            "base_url": base_url_v1,
        }))
        log.info(
            "transsion_login: credentials issued for %s (%s)  models=%d  quota=%s "
            "(persist on user Save)",
            creds["display_name"], email, len(creds["models"]), creds["quota_remain"],
        )

    async def _handle_check_update(self, ws, data: dict) -> None:
        """Check GitHub for latest release and return update status."""
        cfg = self._gateway.base_agent.config.update
        channel = (data.get("channel") or cfg.channel or "stable").strip().lower()
        include_prerelease = channel == "prerelease"
        force = bool(data.get("force", False))
        result = await self._update_service.check_for_update(
            include_prerelease=include_prerelease,
            force=force,
        )
        await ws.send(json.dumps(result))
        if result.get("ok") and result.get("update_available"):
            await ws.send(json.dumps({
                "type": "update_available",
                "current_version": result.get("current_version", ""),
                "latest_version": result.get("latest_version", ""),
                "release_url": result.get("release_url", ""),
                "published_at": result.get("published_at", ""),
                "channel": result.get("channel", "stable"),
            }))

    async def _handle_run_update(self, ws, data: dict) -> None:
        """Execute update command and stream progress."""
        upd_cfg = self._gateway.base_agent.config.update
        force_when_busy = bool(data.get("force_when_busy", False))
        if self._running_sessions and not force_when_busy:
            await ws.send(json.dumps({
                "type": "update_result",
                "ok": False,
                "error": (
                    f"Upgrade blocked: {len(self._running_sessions)} active sessions running. "
                    "Retry with force_when_busy=true to continue."
                ),
                "restart_required": False,
                "command": "",
            }))
            return

        async def emit(stage: str, status: str, message: str) -> None:
            await ws.send(json.dumps({
                "type": "update_progress",
                "stage": stage,
                "status": status,
                "message": message,
            }))

        await ws.send(json.dumps({"type": "update_progress", "stage": "start", "status": "running", "message": "Starting update..."}))
        result = await self._update_executor.run_update(
            on_progress=emit,
            timeout_seconds=int(upd_cfg.upgrade_timeout_seconds or 900),
        )
        await ws.send(json.dumps({
            "type": "update_result",
            "ok": bool(result.get("ok")),
            "error": result.get("error", ""),
            "restart_required": bool(result.get("restart_required", False)),
            "command": result.get("command", ""),
        }))

    def _apply_config(self) -> None:
        """Hot-reload provider and config on the running agent after a config save."""
        try:
            from hushclaw.config.loader import load_config
            new_cfg = load_config()
            from hushclaw.providers.registry import get_provider
            agent = self._gateway.base_agent
            agent.reload_runtime(new_cfg)
            # Keep gateway._config in sync so new dynamic agents (created via
            # create_agent tool) inherit the updated provider, not the stale
            # startup config.
            self._gateway._config = new_cfg
            # Update provider on all already-registered dynamic agent pools so
            # they immediately use the new provider without requiring a restart.
            for _name, _pool in self._gateway._pools.items():
                if _name != "default":
                    _pool._agent.provider = get_provider(new_cfg.provider)
            # Flush all cached AgentLoop sessions so the next request creates a
            # fresh loop bound to the new provider/config (old loops hold a
            # reference to the previous provider object and would keep using it).
            self._gateway.clear_all_cached_loops()
            self._update_service = UpdateService(
                cache_ttl_seconds=max(60, int(new_cfg.update.cache_ttl_seconds or 900)),
            )
            log.info(
                "Config reloaded: provider=%s model=%s (session cache flushed)",
                new_cfg.provider.name, new_cfg.agent.model,
            )
            # Reload connectors so enabling/disabling a channel takes effect
            # without a server restart.  Scheduled as a task because _apply_config
            # is synchronous but connector start/stop is async.
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self._connectors.reload(
                            new_cfg.connectors,
                            self._gateway,
                            webhook_registry=self._webhook_handlers,
                        ),
                        name="connectors-reload",
                    )
            except Exception as conn_exc:
                log.error("Connector reload scheduling error: %s", conn_exc)
        except Exception as exc:
            log.error("Config reload error: %s", exc, exc_info=True)

    async def _handle_list_models(self, ws, data: dict) -> None:
        from hushclaw.config.schema import ProviderConfig
        from hushclaw.providers.registry import get_provider
        base_cfg = self._gateway.base_agent.config.provider
        provider_name = data.get("provider") or base_cfg.name

        # Transsion: model list lives on the control plane (bus-ie), not the AI
        # Router (airouter).  Use the stored access_token to call the same
        # /oneapi/api-credentials/info endpoint that acquire_credentials uses.
        # Prefer the token from the WS message (set before Save) over the one
        # in config (only available after Save).
        if provider_name == "transsion":
            import functools
            from hushclaw.providers.transsion import get_models_from_credentials
            access_token = (
                data.get("access_token") or
                self._gateway.base_agent.config.transsion.access_token
            )
            try:
                models = await asyncio.get_event_loop().run_in_executor(
                    None,
                    functools.partial(get_models_from_credentials, access_token),
                )
                await ws.send(json.dumps({"type": "models", "items": models}))
            except Exception as e:
                log.warning("transsion list_models from control plane failed: %s", e)
                await ws.send(json.dumps({"type": "models", "items": [], "error": str(e)}))
            return

        cfg = ProviderConfig(
            name=provider_name,
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

        try:
            await self._run_test_provider(ws, data, step, finish, loop)
        except Exception as e:
            log.exception("Unexpected error in test_provider")
            try:
                await finish(False, f"Unexpected error: {e}")
            except Exception:
                pass

    async def _run_test_provider(self, ws, data: dict, step, finish, loop) -> None:
        import socket
        import ssl
        import time
        import urllib.error
        import urllib.request
        from urllib.parse import urlparse

        base_cfg = self._gateway.base_agent.config.provider
        base_url  = (data.get("base_url") or base_cfg.base_url or "").strip().rstrip("/")
        api_key   = (data.get("api_key")  or base_cfg.api_key  or "").strip()
        provider_name = (data.get("provider") or base_cfg.name or "").strip()
        model     = (data.get("model") or self._gateway.base_agent.config.agent.model or "").strip()
        # TEX Router uses qualified IDs (azure/...); config may still hold a bare or Claude id.
        if provider_name in ("transsion", "tex"):
            if not model or "/" not in model:
                model = "azure/gpt-4o-mini"
                log.info("test_provider: normalized transsion probe model to %s", model)

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
            addrs = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                ),
                timeout=10,
            )
            if not addrs:
                raise socket.gaierror(f"No addresses returned for {host}")
            ip = addrs[0][4][0]
            ms = int((time.monotonic() - t0) * 1000)
            await step("dns", "ok", "DNS Resolution", f"{host} → {ip}  ({ms} ms)")
        except (asyncio.TimeoutError, TimeoutError):
            await step("dns", "error", "DNS Resolution",
                       f"DNS lookup timed out for '{host}'. Check hostname / network.")
            await finish(False, "DNS resolution timed out.")
            return
        except OSError as e:
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
            list_timeout = 25.0 if provider_name in ("transsion", "tex") else 10.0
            log.info(
                "test_provider: list_models provider=%s timeout=%ss",
                provider_name,
                list_timeout,
            )
            models = await asyncio.wait_for(provider.list_models(), timeout=list_timeout)

            if models:
                await step("auth", "ok", "API Authentication",
                           f"Authenticated · {len(models)} model(s) available")
            else:
                # list_models not implemented — try a 1-token completion
                log.info("test_provider: list_models empty, chat probe model=%r", model)
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
        def _model_id(m) -> str:
            return m.get("id", "") if isinstance(m, dict) else str(m)

        if model and models:
            if any(_model_id(m) == model for m in models):
                await step("model", "ok", "Model Check", f"'{model}' is available")
            else:
                ids = [_model_id(m) for m in models[:5]]
                await step("model", "warn", "Model Check",
                           f"'{model}' not found in model list. Available: {', '.join(ids)}…")
        else:
            await step("model", "skip", "Model Check",
                       "Skipped (model list unavailable or no model specified)")

        await finish(True, "All checks passed.")

    async def _handle_list_skills(self, ws) -> None:
        agent = self._gateway.base_agent
        registry = getattr(agent, "_skill_registry", None)
        items = registry.list_all() if registry else []
        skills_raw = getattr(registry, "_skills", {}) if registry else {}
        existing_names = {str(i.get("name", "")).lower() for i in items}

        # Include memory-defined skills (remember_skill) for visibility in WebUI.
        try:
            mem_skills = self._gateway.memory.search_by_tag("_skill", limit=200)
        except Exception:
            mem_skills = []
        for ms in mem_skills:
            title = str(ms.get("title", "") or "").strip()
            if not title:
                continue
            if title.lower() in existing_names:
                continue
            body = str(ms.get("body", "") or "").strip()
            first_line = body.splitlines()[0].strip() if body else ""
            desc = first_line[:140] + ("…" if len(first_line) > 140 else "") if first_line else "Saved memory skill"
            items.append({
                "name": title,
                "description": desc,
                "builtin": False,
                "tags": ["_skill"],
                "available": True,
                "reason": "",
                "direct_tool": "",
                "author": "",
                "version": "",
                "license": "",
                "homepage": "",
                "source": "memory",
                "install_hints": [],
                "scope": "memory",
                "scope_label": "Memory",
            })
            existing_names.add(title.lower())

        # Merge installed_version from lockfile(s)
        lock: dict = {}
        for skill_dir_path in [agent.config.tools.skill_dir, agent.config.tools.user_skill_dir]:
            if skill_dir_path and skill_dir_path.exists():
                lock.update(self._read_lock(skill_dir_path))
        if lock:
            for item in items:
                entry = lock.get(item["name"])
                if entry:
                    item["installed_version"] = entry.get("version", "")
                    item["installed_at"] = entry.get("installed_at", 0)

        skill_dir_path = agent.config.tools.skill_dir.resolve() if agent.config.tools.skill_dir else None
        user_skill_dir_path = (
            agent.config.tools.user_skill_dir.resolve()
            if agent.config.tools.user_skill_dir else None
        )
        workspace_skill_dir_path = (
            (agent.config.agent.workspace_dir / "skills").resolve()
            if agent.config.agent.workspace_dir else None
        )
        for item in items:
            if item.get("scope") == "memory":
                continue
            raw = skills_raw.get(item.get("name", "")) or {}
            path_str = str(raw.get("path", "") or "")
            scope = "unknown"
            if item.get("builtin"):
                scope = "builtin"
            elif path_str:
                p = Path(path_str).resolve()
                if skill_dir_path and str(p).startswith(str(skill_dir_path)):
                    scope = "system"
                elif user_skill_dir_path and str(p).startswith(str(user_skill_dir_path)):
                    scope = "user"
                elif workspace_skill_dir_path and str(p).startswith(str(workspace_skill_dir_path)):
                    scope = "workspace"
            item["scope"] = scope
            item["scope_label"] = {
                "builtin": "Built-in",
                "system": "System",
                "user": "User",
                "workspace": "Workspace",
                "memory": "Memory",
            }.get(scope, "Unknown")

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
        skill_dir = self._gateway.base_agent.config.tools.skill_dir
        user_skill_dir = self._gateway.base_agent.config.tools.user_skill_dir
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

    # ------------------------------------------------------------------ lockfile

    def _read_lock(self, skill_dir: Path) -> dict:
        """Read .skill-lock.json from skill_dir. Returns {} on any error."""
        lock_path = skill_dir / ".skill-lock.json"
        try:
            if lock_path.exists():
                return json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("Could not read lockfile %s: %s", lock_path, exc)
        return {}

    def _write_lock(self, skill_dir: Path, slug: str, entry: dict) -> None:
        """Upsert one slug entry in .skill-lock.json."""
        lock_path = skill_dir / ".skill-lock.json"
        data = self._read_lock(skill_dir)
        data[slug] = entry
        try:
            lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not write lockfile %s: %s", lock_path, exc)

    # ----------------------------------------------------------- shared post-install

    async def _post_install(
        self,
        ws,
        target_dir: Path,
        slug: str,
        source: str,
        source_type: str,
        agent,
        install_skill_dir: Path,
    ) -> dict:
        """
        Shared post-download processing for both git and zip installs:
          1. pip install requirements.txt (captures stderr)
          2. SkillRegistry reload (Bug-A fix: pass all skill_dirs)
          3. load_plugins with correct namespace
          4. Write .skill-lock.json entry
        Returns a result dict (to be sent as skill_install_result).
        """
        from hushclaw.skills.loader import SkillRegistry

        # ----- 1. pip dependencies ------------------------------------------
        deps_ok: bool | None = None
        deps_error = ""
        req_file = target_dir / "requirements.txt"
        if req_file.exists():
            await ws.send(json.dumps({
                "type": "skill_install_progress",
                "slug": slug,
                "message": "Installing dependencies from requirements.txt…",
            }))
            pip_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-r", str(req_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    pip_proc.communicate(), timeout=120
                )
                deps_ok = (pip_proc.returncode == 0)
                if not deps_ok:
                    deps_error = stderr_b.decode(errors="ignore").strip()[-800:]
            except asyncio.TimeoutError:
                pip_proc.kill()
                deps_ok = False
                deps_error = "pip install timed out after 120 seconds."
                log.warning("pip install timed out for %s", req_file)

        # ----- 2. SkillRegistry reload (Bug-A fix) --------------------------
        skill_dirs = []
        if agent.config.tools.skill_dir:
            skill_dirs.append(agent.config.tools.skill_dir)
        if (
            agent.config.tools.user_skill_dir
            and agent.config.tools.user_skill_dir.exists()
        ):
            skill_dirs.append(agent.config.tools.user_skill_dir)
        if not skill_dirs:
            skill_dirs.append(install_skill_dir)
        agent._skill_registry = SkillRegistry(skill_dirs)
        # Invalidate marketplace cache so installed state refreshes
        self._skill_repo_cache = None
        # Clear all cached AgentLoop objects so next request gets a fresh loop
        # that picks up the updated _skill_registry (loops cache it at creation).
        self._gateway.clear_all_cached_loops()

        # Count skills from this specific install directory
        repo_skill_count = sum(
            1 for s in agent._skill_registry._skills.values()
            if str(target_dir) in s.get("path", "")
        )
        warning = ""
        if repo_skill_count == 0:
            warning = (
                "No SKILL.md files found in this directory. "
                "It may not be a skill package. "
                "Check for a SKILL.md file in the repository root."
            )

        # ----- 3. Load bundled tools (namespace consistency fix) ------------
        bundled_tool_count = 0
        tools_dir = target_dir / "tools"
        if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
            before = len(agent.registry)
            # System skill_dir installs get no namespace; user installs use slug
            is_system = (
                agent.config.tools.skill_dir
                and str(target_dir).startswith(str(agent.config.tools.skill_dir))
            )
            ns = None if is_system else slug
            agent.registry.load_plugins(tools_dir, namespace=ns)
            bundled_tool_count = len(agent.registry) - before

        # ----- 4. Write lockfile --------------------------------------------
        skill_md = target_dir / "SKILL.md"
        installed_version = ""
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.strip().startswith("version:"):
                        installed_version = line.split(":", 1)[1].strip().strip('"').strip("'")
                        break
            except Exception:
                pass
        self._write_lock(install_skill_dir, slug, {
            "source": source,
            "source_type": source_type,
            "version": installed_version,
            "installed_at": int(time.time()),
        })

        return {
            "type": "skill_install_result",
            "ok": True,
            "slug": slug,
            "skill_count": len(agent._skill_registry),
            "repo_skill_count": repo_skill_count,
            "bundled_tool_count": bundled_tool_count,
            "deps_installed": deps_ok,
            "deps_error": deps_error,
            "warning": warning,
        }

    # ----------------------------------------------------------- git install

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

        agent = self._gateway.base_agent
        install_skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
        if not install_skill_dir:
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": (
                    "skill_dir is not configured. Set [tools] skill_dir or user_skill_dir "
                    "in hushclaw.toml, then retry."
                ),
            }))
            return

        repo_name = url.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
        target_dir = install_skill_dir / repo_name

        try:
            install_skill_dir.mkdir(parents=True, exist_ok=True)

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

            result = await self._post_install(
                ws, target_dir, repo_name, url, "git", agent, install_skill_dir
            )
            result["url"] = url
            result["repo"] = repo_name
            await ws.send(json.dumps(result))

        except Exception as exc:
            log.error("install_skill_repo error: %s", exc, exc_info=True)
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": str(exc),
            }))

    # ----------------------------------------------------------- zip install

    async def _handle_install_skill_zip(self, ws, data: dict) -> None:
        import io
        import re
        import urllib.request
        import zipfile
        from hushclaw.util.ssl_context import make_ssl_context

        url  = data.get("url", "").strip()
        slug = data.get("slug", "").strip()

        if not url.startswith("https://") or re.search(r'[\s$;|&<>`\'"\\]', url):
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "Invalid URL. Only plain HTTPS zip URLs are supported.",
            }))
            return

        if not slug or re.search(r'[^a-zA-Z0-9_\-]', slug):
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "Invalid slug. Use only letters, numbers, hyphens, and underscores.",
            }))
            return

        agent = self._gateway.base_agent
        install_skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
        if not install_skill_dir:
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": (
                    "skill_dir is not configured. Set [tools] skill_dir or user_skill_dir "
                    "in hushclaw.toml, then retry."
                ),
            }))
            return

        target_dir = install_skill_dir / slug

        try:
            install_skill_dir.mkdir(parents=True, exist_ok=True)

            await ws.send(json.dumps({
                "type": "skill_install_progress",
                "url": url,
                "message": f"Downloading {slug}…",
            }))

            loop = asyncio.get_event_loop()
            req = urllib.request.Request(url, headers={"User-Agent": "HushClaw/1.0"})

            def _download():
                with urllib.request.urlopen(req, timeout=60, context=make_ssl_context()) as resp:
                    return resp.read()

            try:
                raw_bytes = await asyncio.wait_for(
                    loop.run_in_executor(None, _download), timeout=65
                )
            except asyncio.TimeoutError:
                await ws.send(json.dumps({
                    "type": "skill_install_result",
                    "ok": False,
                    "url": url,
                    "error": "Download timed out after 60 seconds.",
                }))
                return

            await ws.send(json.dumps({
                "type": "skill_install_progress",
                "url": url,
                "message": f"Extracting {slug}…",
            }))

            buf = io.BytesIO(raw_bytes)
            target_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(buf) as zf:
                names = zf.namelist()
                # Auto-strip single wrapper directory (GitHub zip style)
                prefix = names[0].split("/")[0] + "/" if names else ""
                strip = (
                    bool(prefix)
                    and len(prefix) > 1
                    and all(n.startswith(prefix) for n in names)
                )
                for member in zf.infolist():
                    rel = member.filename[len(prefix):] if strip else member.filename
                    if not rel:
                        continue
                    dest = target_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not member.is_dir():
                        dest.write_bytes(zf.read(member.filename))

            result = await self._post_install(
                ws, target_dir, slug, url, "zip", agent, install_skill_dir
            )
            result["url"] = url
            await ws.send(json.dumps(result))

        except Exception as exc:
            log.error("install_skill_zip error: %s", exc, exc_info=True)
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
            agent = self._gateway.base_agent
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

    def _get_or_create_session_entry(self, session_id: str) -> _SessionEntry:
        """Return (or create) the server-level entry for *session_id*.

        If an entry exists with a running task, cancel that task before
        resetting state — a new chat message implies a fresh run.
        """
        entry = self._session_tasks.get(session_id)
        if entry is None:
            entry = _SessionEntry(session_id=session_id)
            self._session_tasks[session_id] = entry
        else:
            if entry.task and not entry.task.done():
                entry.task.cancel()
            entry.task = None
            entry.text = ""
            entry.buffer.clear()
            entry.finished_at = None
        return entry

    async def _subscribe_session(self, ws, session_id: str) -> None:
        """Attach *ws* as subscriber for a running session and replay its buffer."""
        entry = self._session_tasks.get(session_id)
        if entry is None or not entry.is_running():
            await ws.send(json.dumps({
                "type": "session_not_running",
                "session_id": session_id,
                "expired": entry is None,
            }))
            return

        entry.subscriber = ws
        buffered = list(entry.buffer)
        try:
            await ws.send(json.dumps({
                "type": "replay_start",
                "session_id": session_id,
                "count": len(buffered),
            }))
            for raw in buffered:
                await ws.send(raw)
            # Send accumulated partial text as a single chunk so the client can
            # display where the stream was when the connection dropped.
            if entry.text:
                await ws.send(json.dumps({
                    "type": "chunk",
                    "text": entry.text,
                    "_replay": True,
                }))
            await ws.send(json.dumps({
                "type": "replay_end",
                "session_id": session_id,
            }))
        except Exception:
            entry.subscriber = None

    async def _emit_session_status(self, ws, session_id: str, status: str, reason: str) -> None:
        if not session_id:
            return
        if status == "running":
            self._running_sessions.add(session_id)
        elif status in {"idle", "offline", "stale"}:
            self._running_sessions.discard(session_id)
        await ws.send(json.dumps({
            "type": "session_status",
            "session_id": session_id,
            "status": status,
            "reason": reason,
            "ts": int(time.time() * 1000),
        }))

    # ── Attachment processing (multimodal) ────────────────────────────────────

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    _IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}

    @staticmethod
    def _detect_mime(path_str: str, data: bytes) -> str | None:
        """Detect image MIME type from magic bytes or file extension."""
        import os
        sig = data[:16]
        if sig[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if sig[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if sig[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if sig[:4] in (b"RIFF", b"WEBP") or b"WEBP" in sig[:12]:
            return "image/webp"
        ext = os.path.splitext(path_str)[1].lower()
        return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"
                }.get(ext.lstrip("."))

    def _process_attachments(
        self, text: str, attachments: list[dict]
    ) -> tuple[str, list[str]]:
        """Split attachments into (augmented_text, image_data_uris).

        Image attachments are read from disk and returned as base64 data URIs
        for direct LLM vision input.  Non-image attachments are appended to the
        text as local paths so the agent can use read_file().
        """
        import base64
        if not attachments:
            return text, []

        images: list[str] = []
        file_lines: list[str] = []

        for att in attachments:
            name = att.get("name", "file")
            file_id = att.get("file_id", "")
            if file_id:
                matches = list(self._upload_dir.glob(f"{file_id}_*"))
                local_path = str(matches[0]) if matches else ""
            else:
                local_path = ""

            if local_path:
                try:
                    with open(local_path, "rb") as _fh:
                        raw = _fh.read()
                    mime = self._detect_mime(local_path, raw)
                    if mime and mime in self._IMAGE_MIMES:
                        b64 = base64.b64encode(raw).decode()
                        images.append(f"data:{mime};base64,{b64}")
                        log.debug("multimodal: encoded image %s (%d bytes)", name, len(raw))
                        continue
                except Exception as e:
                    log.warning("multimodal: failed to read %s: %s", local_path, e)
                # Non-image or read error — inject path as text
                file_lines.append(f"- {name} (local path: {local_path})")
            else:
                url = att.get("url", "")
                file_lines.append(f"- {name} (url: {url})" if url else f"- {name}")

        if file_lines:
            lines = [text] if text else []
            lines.append("\n[Attached files]")
            lines.extend(file_lines)
            text = "\n".join(lines).strip()

        return text, images

    def _get_skill_registry_for_agent(self, agent_name: str):
        """Return the skill registry for a routed agent (fallback to base agent)."""
        pool = self._gateway.get_pool(agent_name)
        reg = getattr(pool._agent, "_skill_registry", None)
        if reg is not None:
            return reg
        return getattr(self._gateway.base_agent, "_skill_registry", None)

    @staticmethod
    def _rewrite_prompt_skill_text(skill_name: str, skill_desc: str, task: str) -> str:
        """Encode '/<skill>' intent into plain text for prompt-only skills."""
        desc = (skill_desc or "").strip()
        body = task.strip() if task.strip() else (desc or f"Run skill '{skill_name}'.")
        return (
            f"[SkillCommand /{skill_name}] {body}\n"
            f"Please apply the '/{skill_name}' skill instructions for this request."
        )

    async def _try_handle_slash_command(
        self,
        ws,
        agent_name: str,
        session_id: str,
        text: str,
    ) -> tuple[bool, bool, str]:
        """Handle slash commands before LLM routing.

        Returns:
          (handled, ok, next_text)
          - handled=False: caller should continue normal chat flow
          - handled=True: command already responded; caller should return
        """
        if not text.startswith("/"):
            return False, True, text
        raw = text[1:].strip()
        if not raw:
            return False, True, text

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1].strip() if len(parts) > 1 else ""
        if not cmd:
            return False, True, text

        skill_registry = self._get_skill_registry_for_agent(agent_name)
        if skill_registry is None:
            await ws.send(json.dumps({"type": "error", "message": "Skill registry unavailable."}))
            return True, False, text

        if cmd == "skills":
            items = skill_registry.list_all() or []
            items = sorted(items, key=lambda s: (s.get("available") is not True, s.get("name", "")))
            lines = [f"Available skills ({len(items)}):"]
            if not items:
                lines.append("- (none)")
            for s in items:
                name = s.get("name", "")
                desc = s.get("description", "") or "No description."
                if s.get("available", True):
                    lines.append(f"- /{name}: {desc}")
                else:
                    reason = s.get("reason", "requirements not met")
                    lines.append(f"- /{name}: {desc} [unavailable: {reason}]")
            await ws.send(json.dumps({"type": "done", "text": "\n".join(lines)}))
            log.info("slash command handled: /skills session=%s", session_id[:12])
            return True, True, text

        skill = skill_registry.get(cmd)
        if skill is None:
            # Keep compatibility: unknown slash command falls back to normal chat.
            return False, True, text

        if not skill.get("available", True):
            reason = skill.get("reason", "requirements not met")
            await ws.send(json.dumps({"type": "error", "message": f"Skill '/{cmd}' unavailable: {reason}"}))
            log.info("slash command unavailable: /%s session=%s", cmd, session_id[:12])
            return True, False, text

        tool_name = skill.get("direct_tool", "")
        if not tool_name:
            # Fallback for prompt-only skills: route as normal chat with an
            # explicit skill intent so "/<skill>" remains usable in WebUI.
            desc = (skill.get("description", "") or "").strip()
            if not cmd_args:
                self._pending_skill_prompts[session_id] = {"skill": cmd, "description": desc}
                await ws.send(json.dumps({
                    "type": "done",
                    "text": (
                        f"Using '/{cmd}'. Please add one short requirement "
                        "(e.g. time range or focus), then send."
                    ),
                }))
                log.info("slash command prompt-skill awaiting details: /%s session=%s", cmd, session_id[:12])
                return True, True, text
            task = cmd_args or desc or f"Run skill '{cmd}'."
            rewritten = self._rewrite_prompt_skill_text(cmd, desc, task)
            log.info("slash command prompt-skill fallback: /%s session=%s", cmd, session_id[:12])
            return False, True, rewritten

        try:
            pool = self._gateway.get_pool(agent_name)
            loop_obj = pool._get_or_create_loop(session_id, gateway=self._gateway)
            result = await loop_obj.executor.execute_single(tool_name, {})
            if getattr(result, "is_error", False):
                await ws.send(json.dumps({
                    "type": "error",
                    "message": f"Skill '/{cmd}' tool '{tool_name}' failed: {result.content}",
                }))
                log.info("slash command tool error: /%s tool=%s session=%s", cmd, tool_name, session_id[:12])
                return True, False, text
            await ws.send(json.dumps({"type": "done", "text": result.content}))
            log.info("slash command tool executed: /%s tool=%s session=%s", cmd, tool_name, session_id[:12])
            return True, True, text
        except Exception as e:
            log.error("slash command execution error: /%s err=%s", cmd, e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))
            return True, False, text

    async def _handle_chat(self, ws, data: dict, session_ids: dict) -> None:
        import time as _time
        _t_recv = _time.monotonic()

        agent = data.get("agent", "default")
        text = data.get("text", "").strip()

        log.info(
            "chat recv: agent=%s input=%r",
            agent, text[:80],
        )

        # Split attachments: images → vision content blocks, others → path text
        attachments = data.get("attachments") or []
        text, images = self._process_attachments(text, attachments)
        _t_attach = _time.monotonic()
        if attachments:
            log.info(
                "chat attachments: n=%d elapsed=%.0fms",
                len(attachments), (_t_attach - _t_recv) * 1000,
            )

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or session_ids.get(agent) or make_id("s-")
        session_ids[agent] = session_id
        pending = self._pending_skill_prompts.pop(session_id, None)
        if pending and text and not text.startswith("/"):
            text = self._rewrite_prompt_skill_text(
                pending.get("skill", "").strip() or "skill",
                pending.get("description", ""),
                text,
            )
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")

        handled, ok, text = await self._try_handle_slash_command(ws, agent, session_id, text)
        if handled:
            await self._emit_session_status(ws, session_id, "idle", "done" if ok else "error")
            return

        _t_dispatch = _time.monotonic()
        log.info(
            "chat dispatch: agent=%s session=%s pre_dispatch=%.0fms",
            agent, session_id[:12], (_t_dispatch - _t_recv) * 1000,
        )

        _first_event = True
        try:
            async for event in self._gateway.event_stream(agent, text, session_id, images=images):
                if _first_event:
                    _first_event = False
                    log.info(
                        "chat first_event: session=%s type=%s elapsed=%.0fms",
                        session_id[:12], event.get("type"), (_time.monotonic() - _t_recv) * 1000,
                    )
                await ws.send(json.dumps(event))
                if event.get("type") == "done":
                    log.info(
                        "chat done: session=%s total=%.0fms",
                        session_id[:12], (_time.monotonic() - _t_recv) * 1000,
                    )
                    await self._emit_session_status(ws, session_id, "idle", "done")
                elif event.get("type") == "error":
                    await self._emit_session_status(ws, session_id, "idle", "error")
        except Exception as e:
            log.error("event_stream error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_broadcast_mention(self, ws, data: dict, session_ids: dict) -> None:
        text = data.get("text", "").strip()
        agents_raw = data.get("agents", [])
        if isinstance(agents_raw, str):
            agent_names = [a.strip() for a in agents_raw.split(",") if a.strip()]
        elif isinstance(agents_raw, list):
            agent_names = [str(a).strip() for a in agents_raw if str(a).strip()]
        else:
            await ws.send(json.dumps({"type": "error", "message": "agents must be a list or string"}))
            return
        agent_names = list(dict.fromkeys(agent_names))

        # Split attachments: images → vision, others → path text
        attachments = data.get("attachments") or []
        text, _images = self._process_attachments(text, attachments)

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return
        if not agent_names:
            await ws.send(json.dumps({"type": "error", "message": "agents is required"}))
            return

        unknown = [name for name in agent_names if self._gateway.get_agent_def(name) is None]
        if unknown:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown agents: {', '.join(unknown)}"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")
        log.info(
            "mention routing: mode=broadcast agents=%s fallback=default session=%s",
            agent_names,
            session_id[:12],
        )
        try:
            results = await self._gateway.broadcast(agent_names, text)
            merged = "\n\n".join(
                f"### @{name}\n{(results.get(name) or '').strip()}" for name in agent_names
            ).strip()
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": merged or "(empty broadcast response)"}))
        except Exception as e:
            log.error("broadcast_mention error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
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
        await self._emit_session_status(ws, session_id, "running", "start")

        try:
            async for event in self._gateway.pipeline_stream(agent_names, text, session_id):
                await ws.send(json.dumps(event))
                if event.get("type") == "done":
                    await self._emit_session_status(ws, session_id, "idle", "done")
                elif event.get("type") == "error":
                    await self._emit_session_status(ws, session_id, "idle", "error")
        except Exception as e:
            log.error("pipeline_stream error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_orchestrate(self, ws, data: dict, session_ids: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")

        try:
            result = await self._gateway.orchestrate(text, session_id)
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("orchestrate error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
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
        await self._emit_session_status(ws, session_id, "running", "start")
        try:
            result = await self._gateway.execute_hierarchical(
                commander_name=commander,
                text=text,
                mode=mode,
                session_id=session_id,
            )
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("run_hierarchical error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

