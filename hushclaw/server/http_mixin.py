"""server/http_mixin.py — HTTP handler, static file serving, upload, HTTP API proxy,
config file watcher, and WebSocket upload handler.

Extracted from server_impl.py. All methods are accessed via self (mixin pattern).
Module-level constants (_WEB_DIR, _MIME, _INLINE_SUFFIXES, _make_response) are
defined here since their only callers moved to this mixin.
"""
from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from pathlib import Path

from hushclaw.util.logging import get_logger

log = get_logger("server")

_WEB_DIR = Path(__file__).parent.parent / "web"

_MIME = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
    ".otf":  "font/otf",
    ".eot":  "application/vnd.ms-fontobject",
    ".map":  "application/json",
    ".wasm": "application/wasm",
    ".mp4":  "video/mp4",
    ".mp3":  "audio/mpeg",
    ".webm": "video/webm",
    ".ogg":  "audio/ogg",
    ".wav":  "audio/wav",
}

# File types that browsers can render directly. HTML is included so generated
# reports/decks can load sibling assets from the same /files/ directory instead
# of being downloaded as a standalone file.
_INLINE_SUFFIXES = {
    ".html", ".htm",
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp4", ".mp3", ".webm", ".ogg", ".wav",
}


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


class HttpMixin:
    """Mixin for HushClawServer: HTTP serving, file upload, HTTP API proxy, config watcher."""

    # ── Community / auth API base URLs ────────────────────────────────────────
    _COMMUNITY_BASE = "https://bus-ie.aibotplatform.com/hushclaw/community/api/v1/community"
    _AUTH_BASE       = "https://bus-ie.aibotplatform.com/assistant/vendor-api/v1/auth"

    # ── HTTP handler (websockets process_request hook) ─────────────────────────

    async def _http_handler(self, connection, request):
        """websockets asyncio process_request hook: serve static files, webhooks, WS upgrades."""
        try:
            if request.headers.get("upgrade", "").lower() == "websocket":
                return None  # let websockets handle WS upgrade normally

            full_path = request.path
            path      = full_path.split("?")[0]
            query     = full_path.split("?", 1)[1] if "?" in full_path else ""
            method    = getattr(request, "method", "GET").upper()

            # ── File upload (PUT /upload?name=filename) ──────────────────────
            if path == "/upload" and method == "PUT":
                return await self._handle_upload(connection, request, query)

            # ── File download (GET /files/<file_id_name>) ────────────────────
            if path.startswith("/files/") and method == "GET":
                return await self._serve_file(request, query, path[7:])

            # ── Webhook routing (POST /webhook/<platform>) ───────────────────
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

            # ── Static file serving ──────────────────────────────────────────
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

    # ── HTTP API server (POST proxy for community / auth APIs) ─────────────────
    # websockets 16 rejects non-GET methods before process_request is called.
    # This minimal asyncio stream server runs on port+1 and handles POST/OPTIONS.

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

    # ── Config file watcher ────────────────────────────────────────────────────

    async def _start_config_watcher(self) -> None:
        """Start a background task that polls the config file every 15 seconds."""
        from hushclaw.config.loader import get_config_dir
        self._config_file_path = get_config_dir() / "hushclaw.toml"
        try:
            self._config_file_mtime = self._config_file_path.stat().st_mtime
        except OSError:
            self._config_file_mtime = 0.0
        self._config_watcher_task = asyncio.create_task(self._config_watcher_loop())

    async def _background_startup(self) -> None:
        """Non-critical startup tasks deferred until after the WebSocket is ready.

        Runs 2 seconds after the server starts so the first browser connection
        is never blocked by connector initialisation (which may install packages
        or do initial network I/O) or other background services.
        """
        await asyncio.sleep(2)
        try:
            await self._scheduler.start()
        except Exception as exc:
            log.error("Background startup: scheduler failed to start: %s", exc)
        try:
            await self._connectors.start()
        except Exception as exc:
            log.error("Background startup: connectors failed to start: %s", exc)
        try:
            await self._start_config_watcher()
        except Exception as exc:
            log.error("Background startup: config watcher failed to start: %s", exc)
        log.info("Background startup complete.")

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

    # ── File upload via WebSocket ──────────────────────────────────────────────

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

    # ── File listing via WebSocket ─────────────────────────────────────────────

    async def _handle_list_files(self, ws, data: dict) -> None:
        """Handle {type: "list_files"} — return paginated upload directory listing."""
        upload_dir = self._upload_dir
        limit = max(1, min(int(data.get("limit", 50)), 200))
        offset = max(0, int(data.get("offset", 0)))
        items = []
        if upload_dir.exists():
            for p in upload_dir.iterdir():
                if not p.is_file() or p.name.startswith("."):
                    continue
                stat = p.stat()
                parts = p.name.split("_", 1)
                display = parts[1] if len(parts) == 2 else p.name
                items.append({
                    "name": display,
                    "filename": p.name,
                    "url": f"/files/{p.name}",
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                })
        items.sort(key=lambda x: x["modified"], reverse=True)
        total = len(items)
        await self._send_json(ws, {
            "type": "files",
            "items": items[offset: offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < total,
        })

    async def _handle_ingest_file(self, ws, data: dict) -> None:
        """Index an already-uploaded file into the knowledge base (notes + vector)."""
        filename = (data.get("filename") or "").strip()
        if not filename:
            await self._send_json(ws, {"type": "file_ingested", "ok": False, "error": "Missing filename"})
            return
        p = (self._upload_dir / filename).resolve()
        try:
            p.relative_to(self._upload_dir.resolve())
        except ValueError:
            await self._send_json(ws, {"type": "file_ingested", "ok": False, "error": "Invalid path"})
            return
        if not p.exists():
            await self._send_json(ws, {"type": "file_ingested", "ok": False, "error": "File not found"})
            return
        parts = filename.split("_", 1)
        display = parts[1] if len(parts) == 2 else filename
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            memory = self._gateway.base_agent.memory
            note_id = memory.remember(
                content,
                title=display,
                tags=["file", "uploaded"],
                scope="global",
                persist_to_disk=False,
                note_type="fact",
                memory_kind="project_knowledge",
            )
            await self._send_json(ws, {"type": "file_ingested", "ok": True, "filename": filename, "note_id": note_id})
        except Exception as exc:
            await self._send_json(ws, {"type": "file_ingested", "ok": False, "error": str(exc)})

    async def _handle_delete_file(self, ws, data: dict) -> None:
        """Delete an uploaded file from disk and remove its knowledge index entry."""
        filename = (data.get("filename") or "").strip()
        if not filename:
            await self._send_json(ws, {"type": "file_deleted", "ok": False, "error": "Missing filename"})
            return
        p = (self._upload_dir / filename).resolve()
        try:
            p.relative_to(self._upload_dir.resolve())
        except ValueError:
            await self._send_json(ws, {"type": "file_deleted", "ok": False, "error": "Invalid path"})
            return
        try:
            if p.exists():
                p.unlink()
            # Remove matching knowledge index entry (indexed by display name as title)
            parts = filename.split("_", 1)
            display = parts[1] if len(parts) == 2 else filename
            memory = self._gateway.base_agent.memory
            rows = memory.conn.execute(
                "SELECT note_id FROM notes WHERE title=? AND tags LIKE '%\"file\"%'",
                (display,),
            ).fetchall()
            for row in rows:
                memory.delete_note(row["note_id"])
            await self._send_json(ws, {"type": "file_deleted", "ok": True, "filename": filename})
        except Exception as exc:
            await self._send_json(ws, {"type": "file_deleted", "ok": False, "error": str(exc)})

    # ── File upload / download (HTTP PUT / GET) ────────────────────────────────

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

        file_id  = uuid4().hex[:12]
        filename = f"{file_id}_{safe_name}"
        (self._upload_dir / filename).write_bytes(file_bytes)
        log.info("Uploaded file (HTTP PUT): %s (%d bytes)", filename, len(file_bytes))

        resp_body = json.dumps({
            "ok": True, "file_id": file_id,
            "name": safe_name, "url": f"/files/{filename}",
            "size": len(file_bytes),
        }).encode()
        return _make_response(HTTPStatus.OK, [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(resp_body))),
            ("Connection", "close"),
        ], resp_body)

    async def _serve_file(self, request, query: str, fid_path: str):
        """Serve a previously uploaded file by file_id or artifact path."""
        if not self._check_http_auth(request, query):
            return _make_response(HTTPStatus.UNAUTHORIZED, [
                ("Content-Type", "text/plain"),
                ("Connection", "close"),
            ], b"Unauthorized")

        if not fid_path:
            return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")

        target = None

        if fid_path.startswith("artifacts/"):
            rel = Path(fid_path)
            if rel.is_absolute() or ".." in rel.parts:
                return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")
            if len(rel.parts) < 2:
                return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")
            artifacts_root = (self._upload_dir / "artifacts").resolve()
            artifact_root = (artifacts_root / rel.parts[1]).resolve()
            try:
                candidate = artifact_root
                if len(rel.parts) > 2:
                    candidate = (artifact_root / Path(*rel.parts[2:])).resolve()
                candidate.relative_to(artifact_root)
                if candidate.is_dir():
                    candidate = (candidate / "index.html").resolve()
                    candidate.relative_to(artifact_root)
            except Exception:
                return _make_response(HTTPStatus.NOT_FOUND, [("Connection", "close")], b"Not found")
            target = candidate
        else:
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
        disposition = "inline" if target.suffix.lower() in _INLINE_SUFFIXES else "attachment"

        return _make_response(HTTPStatus.OK, [
            ("Content-Type", mime),
            ("Content-Length", str(len(file_bytes))),
            ("Content-Disposition", f'{disposition}; filename="{display_name}"'),
            ("Connection", "close"),
        ], file_bytes)
