"""HushClaw WebSocket server — requires 'websockets>=12.0' (pip install hushclaw[server])."""
from __future__ import annotations

import asyncio
import json
import os
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
from hushclaw.config.writer import dict_to_toml_str
from hushclaw.memory.kinds import ALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger
from hushclaw.update import UpdateExecutor, UpdateService
from hushclaw.server import provider_handler, skill_handler, transsion_handler, config_handler, update_handler
from hushclaw._build_info import BUILD_TIME as _BUILD_TIME

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

    async def _handle_list_sessions(self, ws, data: dict) -> None:
        gw_cfg = self._gateway.base_agent.config.gateway
        limit = int(data.get("limit", gw_cfg.session_list_limit))
        include_scheduled = data.get("include_scheduled", not gw_cfg.session_list_hide_scheduled)
        max_idle_days = int(data.get("max_idle_days", gw_cfg.session_list_idle_days))
        workspace_filter = self._clean_optional_text(data.get("workspace"))
        items = self._gateway.memory.list_sessions(
            limit=max(1, limit),
            include_scheduled=bool(include_scheduled),
            max_idle_days=max(0, max_idle_days),
            workspace=workspace_filter,
        )
        await self._send_json(ws, {"type": "sessions", "items": items})

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
        # Config file watcher state
        self._config_file_path: Path | None = None
        self._config_file_mtime: float = 0.0
        self._config_watcher_task: asyncio.Task | None = None
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
        if bool(SYSTEM_MEMORY_TAGS & set(tags)):
            return True
        return item.get("memory_kind") in {"telemetry", "session_memory"}

    @staticmethod
    def _is_compacted_auto_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_compact" in tags

    @staticmethod
    def _normalize_memory_kind_filter(raw) -> set[str]:
        if raw is None or raw == "":
            return USER_VISIBLE_MEMORY_KINDS
        values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        values = [str(v).strip() for v in values if str(v).strip()]
        if any(v == "all" for v in values):
            return set(ALL_MEMORY_KINDS)
        normalized = {v for v in values if v in ALL_MEMORY_KINDS}
        return normalized or USER_VISIBLE_MEMORY_KINDS

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

    async def _compact_auto_memories(self, *, group_limit: int = 24) -> dict:
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

            # LLM-based semantic distillation: compress bullet list into a
            # deduplicated, structured paragraph/bullets using the AI.
            # Falls back to the regex-deduped bullet list if LLM is unavailable.
            try:
                from hushclaw.providers.base import Message as _Msg
                provider = self._gateway.base_agent.provider
                model = self._gateway.base_agent.config.agent.model
                distill_prompt = (
                    f"以下是 {day} 从对话中自动提取的零散记忆条目，请语义去重并提炼为简洁摘要。\n"
                    "要求：合并相似内容，删除重复或低价值条目，用 2-6 个 bullet 列出核心事实。\n"
                    "直接输出 bullet 列表，不要前言和解释。\n\n"
                    + content
                )
                resp = await provider.complete(
                    messages=[_Msg(role="user", content=distill_prompt)],
                    system="You are a memory curator. Output only a concise bullet list of unique facts.",
                    max_tokens=400,
                    model=model,
                )
                if resp.content and resp.content.strip():
                    content = resp.content.strip()
            except Exception as _e:
                log.warning(
                    "compact_auto_memories: LLM distillation failed, using regex result: %s", _e
                )

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

    async def _dispatch(self, ws, data: dict) -> None:
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
                    model=data.get("model", ""),
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
                    model=data.get("model"),
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
            "version":    self._update_service.current_version,
            "build_time": _BUILD_TIME,
            "configured": (not needs_key) or bool(api_key),
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "public_base_url": cfg.server.public_base_url or "",
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "cheap_model": cfg.agent.cheap_model or "",
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
                    "workspace":       tg.workspace,
                    "allowlist":       tg.allowlist,
                    "group_allowlist": tg.group_allowlist,
                    "group_policy":    tg.group_policy,
                    "require_mention": tg.require_mention,
                    "markdown":        tg.markdown,
                },
                "feishu": {
                    "enabled":                fs.enabled,
                    "app_id":                 fs.app_id,
                    "app_secret_set":         bool(fs.app_secret),
                    "encrypt_key_set":        bool(fs.encrypt_key),
                    "verification_token_set": bool(fs.verification_token),
                    "agent":                  fs.agent,
                    "workspace":              fs.workspace,
                    "allowlist":              fs.allowlist,
                    "markdown":               fs.markdown,
                },
                "discord": {
                    "enabled":         dc.enabled,
                    "bot_token_set":   bool(dc.bot_token),
                    "agent":           dc.agent,
                    "workspace":       dc.workspace,
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
                    "workspace":     sl.workspace,
                    "allowlist":     sl.allowlist,
                    "stream":        sl.stream,
                    "markdown":      sl.markdown,
                },
                "dingtalk": {
                    "enabled":           dt.enabled,
                    "client_id":         dt.client_id,
                    "client_secret_set": bool(dt.client_secret),
                    "agent":             dt.agent,
                    "workspace":         dt.workspace,
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
                    "workspace":        wc.workspace,
                    "allowlist":        wc.allowlist,
                    "markdown":         wc.markdown,
                },
            },
            "connector_status": self._connectors.status(),
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
            "workspaces": [
                {
                    "name":        ws.name,
                    "path":        ws.path,
                    "description": ws.description,
                }
                for ws in cfg.workspaces.list
            ],
            # Free-form API keys for skills/integrations.
            # Values masked: only set/unset exposed.
            "api_keys": {
                k: bool(v) for k, v in (cfg.api_keys or {}).items()
            },
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
        await config_handler.handle_init_workspace(ws, data, self._gateway)

    async def _handle_save_config(self, ws, data: dict) -> None:
        await config_handler.handle_save_config(ws, data, self._apply_config)

    async def _handle_save_update_policy(self, ws, data: dict) -> None:
        await config_handler.handle_save_update_policy(ws, data, self._apply_config)

    # ── Transsion / TEX AI Router auth flow ───────────────────────────────────

    async def _handle_transsion_send_code(self, ws, data: dict) -> None:
        await transsion_handler.handle_send_code(ws, data)

    async def _handle_transsion_login(self, ws, data: dict) -> None:
        await transsion_handler.handle_login(ws, data)

    async def _handle_transsion_quota(self, ws, data: dict) -> None:
        await transsion_handler.handle_quota(ws, data, self._gateway)

    async def _handle_check_update(self, ws, data: dict) -> None:
        await update_handler.handle_check_update(ws, data, self._gateway, self._update_service)

    async def _handle_run_update(self, ws, data: dict) -> None:
        await update_handler.handle_run_update(
            ws, data, self._gateway, self._update_executor,
            self._upgrade_lock, self._upgrade_state,
            self._running_sessions, self._connected_clients,
        )
        self._upgrade_in_progress = self._upgrade_state["in_progress"]

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
        await provider_handler.handle_test_provider(ws, data, self._gateway)

    async def _handle_list_skills(self, ws) -> None:
        await skill_handler.handle_list_skills(ws, self._gateway)

    async def _handle_save_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_save_skill(ws, data, self._gateway)

    async def _handle_delete_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_delete_skill(ws, data, self._gateway)

    async def _handle_install_skill_repo(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_repo(ws, data, self._gateway)

    async def _handle_install_skill_zip(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_zip(ws, data, self._gateway)

    async def _handle_export_skills(self, ws, data: dict) -> None:
        await skill_handler.handle_export_skills(ws, data, self._gateway)

    async def _handle_import_skill_zip_upload(self, ws, data: dict) -> None:
        await skill_handler.handle_import_skill_zip(ws, data, self._gateway)

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

    async def _handle_chat(self, ws, data: dict) -> None:
        import time as _time
        _t_recv = _time.monotonic()

        agent = data.get("agent", "default")
        text = data.get("text", "").strip()
        workspace = (data.get("workspace") or "").strip() or None

        log.info(
            "chat recv: agent=%s input=%r workspace=%r",
            agent, text[:80], workspace,
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

        # Validate workspace name against registry (unknown names are silently dropped)
        if workspace:
            known = {ws_entry.name for ws_entry in self._gateway.base_agent.config.workspaces.list}
            if workspace not in known:
                log.warning("chat: unknown workspace=%r, ignoring (known=%s)", workspace, known)
                workspace = None

        session_id = data.get("session_id") or make_id("s-")
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
            "chat dispatch: agent=%s session=%s workspace=%r pre_dispatch=%.0fms",
            agent, session_id[:12], workspace, (_t_dispatch - _t_recv) * 1000,
        )

        _first_event = True
        try:
            async for event in self._gateway.event_stream(agent, text, session_id, images=images, workspace=workspace):
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
                elif event.get("type") == "tool_result" and event.get("tool") == "remember_skill":
                    # Push refreshed skills list so the Skills panel updates without a tab switch
                    await self._handle_list_skills(ws)
                elif event.get("type") == "error":
                    await self._emit_session_status(ws, session_id, "idle", "error")
        except Exception as e:
            log.error("event_stream error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_broadcast_mention(self, ws, data: dict) -> None:
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

    async def _handle_pipeline(self, ws, data: dict) -> None:
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

    async def _handle_orchestrate(self, ws, data: dict) -> None:
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

    async def _handle_run_hierarchical(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return
        commander = (data.get("commander") or "").strip()
        if not commander:
            await ws.send(json.dumps({"type": "error", "message": "commander is required"}))
            return
        mode = (data.get("mode") or "parallel").strip().lower()
        session_id = data.get("session_id") or make_id("s-")
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
