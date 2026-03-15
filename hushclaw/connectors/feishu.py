"""Feishu (Lark) connector — WebSocket long-connection via websockets library."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from hushclaw.connectors.base import Connector, log
from hushclaw.util.ssl_context import make_ssl_context


class FeishuConnector(Connector):
    """Connects to the Feishu open platform via WebSocket and replies to im.message events."""

    OPEN_API = "https://open.feishu.cn/open-apis"
    # Refresh token every 100 minutes (tokens are valid for 2 hours)
    TOKEN_TTL = 6_000

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._app_id: str = config.app_id
        self._app_secret: str = config.app_secret
        self._allowlist: list[str] = list(config.allowlist)
        self._access_token: str = ""
        self._running = False
        self._token_task: asyncio.Task | None = None
        self._ws_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._token_task = asyncio.create_task(self._token_loop(), name="feishu-token")
        # Give the token loop a moment to fetch the first token
        await asyncio.sleep(1)
        self._ws_task = asyncio.create_task(self._ws_loop(), name="feishu-ws")
        log.info("[feishu] connector started (stream=%s)", self._stream)

    async def stop(self) -> None:
        self._running = False
        tasks = [t for t in (self._token_task, self._ws_task) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("[feishu] connector stopped")

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _token_loop(self) -> None:
        while self._running:
            try:
                self._access_token = await asyncio.to_thread(self._fetch_token)
                log.debug("[feishu] access token refreshed")
            except Exception as exc:
                log.error("[feishu] token refresh failed: %s", exc)
            try:
                await asyncio.sleep(self.TOKEN_TTL)
            except asyncio.CancelledError:
                break

    def _fetch_token(self) -> str:
        url = f"{self.OPEN_API}/auth/v3/tenant_access_token/internal"
        data = json.dumps({"app_id": self._app_id, "app_secret": self._app_secret}).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())["tenant_access_token"]

    # ------------------------------------------------------------------
    # WebSocket long-connection loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        import websockets  # type: ignore[import-untyped]

        backoff = 5
        while self._running:
            try:
                endpoint = await asyncio.to_thread(self._get_ws_endpoint)
                async with websockets.connect(
                    endpoint,
                    additional_headers={"Authorization": f"Bearer {self._access_token}"},
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    backoff = 5  # reset on successful connect
                    async for raw in ws:
                        await self._on_event(json.loads(raw))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[feishu] ws error: %s — reconnect in %ds", exc, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    def _get_ws_endpoint(self) -> str:
        url = f"{self.OPEN_API}/event/v1/ws/endpoint"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            data = json.loads(resp.read())
            return data["data"]["url"]

    async def _on_event(self, data: dict) -> None:
        header = data.get("header", {})
        if header.get("event_type") != "im.message.receive_v1":
            return
        event = data.get("event", {})
        msg = event.get("message", {})
        if msg.get("message_type") != "text":
            return
        chat_id: str = msg.get("chat_id", "")
        try:
            text = json.loads(msg.get("content", "{}")).get("text", "").strip()
        except Exception:
            return
        if not text:
            return
        if self._allowlist and chat_id not in self._allowlist:
            return
        asyncio.create_task(
            self._handle_message(chat_id, text),
            name=f"feishu-msg-{chat_id}",
        )

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        """Send first message or patch an existing one. handle = {"message_id": str}."""
        if handle is None:
            msg_id = await asyncio.to_thread(self._send_text, chat_id, text)
            return {"message_id": msg_id}
        try:
            await asyncio.to_thread(self._patch_message, handle["message_id"], text)
        except Exception:
            pass  # patch failure is non-fatal; final send will deliver full text
        return handle

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        text = text or "(无响应)"
        if handle is None:
            await asyncio.to_thread(self._send_text, chat_id, text)
        else:
            try:
                await asyncio.to_thread(self._patch_message, handle["message_id"], text)
            except Exception:
                # Fallback: send a new message if patch fails
                await asyncio.to_thread(self._send_text, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level API wrappers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _send_text(self, chat_id: str, text: str) -> str:
        """Send a new text message; returns message_id."""
        url = f"{self.OPEN_API}/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())["data"]["message_id"]

    def _patch_message(self, message_id: str, text: str) -> None:
        """Patch (update) an existing message with new text content."""
        url = f"{self.OPEN_API}/im/v1/messages/{message_id}"
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            json.loads(resp.read())
