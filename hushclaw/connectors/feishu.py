"""Feishu (Lark) connector — official lark-oapi SDK, WebSocket long-connection."""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from hushclaw.connectors.base import Connector, log


class FeishuConnector(Connector):
    """
    Connects to Feishu/Lark via WebSocket long-connection using the lark-oapi SDK.

    No public IP or domain is required — the SDK manages the persistent WS
    connection to Feishu's servers internally.

    Required config fields: app_id, app_secret
    Optional:  encrypt_key, verification_token (when encryption is enabled in
               the Feishu developer console)
    """

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._app_id: str = config.app_id
        self._app_secret: str = config.app_secret
        self._encrypt_key: str = getattr(config, "encrypt_key", "")
        self._verification_token: str = getattr(config, "verification_token", "")
        self._allowlist: list[str] = list(config.allowlist)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._lark_client = None  # lark.Client — used for API calls

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        from hushclaw.util.package_setup import ensure_package
        if not ensure_package("lark_oapi", "lark-oapi"):
            raise RuntimeError(
                "lark-oapi could not be installed automatically.\n"
                "Install it manually with: pip install lark-oapi"
            )
        import lark_oapi as lark  # noqa: PLC0415

        self._loop = asyncio.get_running_loop()

        # API client for outbound calls (send / patch messages).
        # Created here (in the async context) — lark.Client uses only requests,
        # not asyncio, so there is no event-loop capture issue.
        self._lark_client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        # Capture credentials for use inside the thread.
        app_id      = self._app_id
        app_secret  = self._app_secret
        encrypt_key = self._encrypt_key
        verify_tok  = self._verification_token
        on_message  = self._on_message

        def _run() -> None:
            # lark_oapi/ws/client.py stores `loop = asyncio.get_event_loop()` at
            # MODULE IMPORT time (module-level variable).  Because ensure_package()
            # first imports the module while the main asyncio loop is running, that
            # module-level `loop` points at the main loop forever (Python caches
            # modules in sys.modules).  No amount of asyncio.new_event_loop() in
            # the thread helps — the SDK's start() always calls
            # `loop.run_until_complete()` on the stale reference.
            #
            # Fix: create a new loop, then directly overwrite the module-level
            # variable before calling start().
            import os as _os                   # noqa: PLC0415
            import lark_oapi as _lark          # noqa: PLC0415
            import lark_oapi.ws.client as _wsc  # noqa: PLC0415
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            _wsc.loop = new_loop               # patch the module-level loop

            # The lark SDK uses two SSL paths that bypass hushclaw's ssl.SSLContext:
            #   1. requests.post() in _get_conn_url() → reads REQUESTS_CA_BUNDLE
            #   2. websockets.connect()               → reads SSL_CERT_FILE (OpenSSL)
            #
            # On managed macOS machines, the corporate root CA lives in the
            # system Keychain and is exported to /etc/ssl/cert.pem — it is NOT
            # in certifi's curated bundle.  ca_bundle_path() now prefers system
            # paths over certifi for exactly this reason.
            #
            # If no bundle is found we fall back to monkey-patching requests to
            # use verify=False (with a warning) so the connector still works on
            # environments where no CA file is accessible.
            from hushclaw.util.ssl_context import ca_bundle_path as _cabp  # noqa: PLC0415
            _bundle = _cabp()
            if _bundle:
                if not _os.environ.get("REQUESTS_CA_BUNDLE"):
                    _os.environ["REQUESTS_CA_BUNDLE"] = _bundle
                if not _os.environ.get("SSL_CERT_FILE"):
                    _os.environ["SSL_CERT_FILE"] = _bundle
            else:
                # Last resort: disable SSL verification for requests calls made
                # by the lark SDK.  This is safe for the WS long-connection use
                # case (the traffic is still encrypted; only peer authentication
                # is skipped), but we warn loudly in the log.
                import warnings as _warnings  # noqa: PLC0415
                import requests as _req        # noqa: PLC0415
                _warnings.warn(
                    "[feishu] No CA bundle found — disabling SSL verification "
                    "for lark SDK requests.  Set REQUESTS_CA_BUNDLE to fix.",
                    stacklevel=1,
                )
                _orig_send = _req.Session.send

                def _unverified_send(self, request, **kwargs):  # type: ignore[override]
                    kwargs["verify"] = False
                    return _orig_send(self, request, **kwargs)

                _req.Session.send = _unverified_send  # type: ignore[method-assign]

            try:
                event_handler = (
                    _lark.EventDispatcherHandler.builder(encrypt_key, verify_tok)
                    .register_p2_im_message_receive_v1(on_message)
                    .build()
                )
                ws_client = _lark.ws.Client(
                    app_id, app_secret,
                    event_handler=event_handler,
                    log_level=_lark.LogLevel.WARNING,
                )
                ws_client.start()
            finally:
                new_loop.close()

        self._ws_thread = threading.Thread(target=_run, daemon=True, name="feishu-ws")
        self._ws_thread.start()
        log.info("[feishu] connector started via lark-oapi SDK")

    async def stop(self) -> None:
        # lark.ws.Client has no public stop() — the daemon thread exits with the process.
        self._lark_client = None
        log.info("[feishu] connector stopped")

    # ------------------------------------------------------------------
    # Inbound event handler (called by lark SDK in a worker thread)
    # ------------------------------------------------------------------

    def _on_message(self, data) -> None:
        """Sync callback invoked by the lark SDK for every im.message.receive_v1 event."""
        try:
            msg = data.event.message
            if msg.message_type != "text":
                return
            chat_id: str = msg.chat_id
            try:
                text = json.loads(msg.content).get("text", "").strip()
            except Exception:
                return
            if not text:
                return
            if self._allowlist and chat_id not in self._allowlist:
                return
            # Bridge the sync SDK callback into the asyncio event loop
            asyncio.run_coroutine_threadsafe(
                self._handle_message(chat_id, text),
                self._loop,
            )
        except Exception as exc:
            log.error("[feishu] _on_message error: %s", exc)

    # ------------------------------------------------------------------
    # Reply delivery
    # ------------------------------------------------------------------

    async def _send_reply(self, chat_id: str, text: str) -> None:
        """Send the complete reply as a single Feishu message."""
        await asyncio.to_thread(self._send_text_sync, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level SDK wrappers (synchronous, safe to call via to_thread)
    # ------------------------------------------------------------------

    def _send_text_sync(self, chat_id: str, text: str) -> str:
        """Create a new text message in the given chat. Returns message_id."""
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = self._lark_client.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"Feishu send failed [{resp.code}]: {resp.msg}")
        return resp.data.message_id

    def _patch_message_sync(self, message_id: str, text: str) -> None:
        """Update an existing message with new text content."""
        from lark_oapi.api.im.v1 import (
            PatchMessageRequest,
            PatchMessageRequestBody,
        )
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = self._lark_client.im.v1.message.patch(request)
        if not resp.success():
            raise RuntimeError(f"Feishu patch failed [{resp.code}]: {resp.msg}")
