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

        self._loop = asyncio.get_running_loop()

        # API client for outbound calls (send / patch messages)
        self._lark_client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        # Event dispatcher — register message-receive handler
        event_handler = (
            lark.EventDispatcherHandler.builder(
                self._encrypt_key,
                self._verification_token,
            )
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )

        # WebSocket client — start() is blocking, run in a daemon thread
        ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )
        self._ws_thread = threading.Thread(
            target=ws_client.start,
            daemon=True,
            name="feishu-ws",
        )
        self._ws_thread.start()
        log.info("[feishu] connector started via lark-oapi SDK (stream=%s)", self._stream)

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
    # Streaming helpers (async, called from base._handle_message)
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        """Send first chunk or patch the existing message for streaming effect."""
        if handle is None:
            msg_id = await asyncio.to_thread(self._send_text_sync, chat_id, text)
            return {"message_id": msg_id}
        try:
            await asyncio.to_thread(self._patch_message_sync, handle["message_id"], text)
        except Exception:
            pass  # patch failure is non-fatal; _send_final delivers the full text
        return handle

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        """Deliver the complete reply."""
        text = text or "(无响应)"
        if handle is None:
            await asyncio.to_thread(self._send_text_sync, chat_id, text)
        else:
            try:
                await asyncio.to_thread(self._patch_message_sync, handle["message_id"], text)
            except Exception:
                # Fallback: send a new message if patch fails
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
