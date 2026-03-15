"""DingTalk Stream connector — WebSocket stream mode, zero extra deps beyond websockets."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from ghostclaw.connectors.base import Connector, log
from ghostclaw.util.ssl_context import make_ssl_context

API_BASE    = "https://api.dingtalk.com"
MAX_MSG_LEN = 3000  # DingTalk practical text limit


class DingTalkConnector(Connector):
    """
    Connects to DingTalk via WebSocket Stream Mode (stream SDK replacement).
    No public HTTP endpoint required — DingTalk initiates the WebSocket connection.

    Setup:
    1. Create an app in DingTalk Open Platform (https://open.dingtalk.com)
    2. Enable "Stream Push Mode" in Subscription Management
    3. Subscribe to /v1.0/im/bot/messages/get event
    4. Set client_id (App Key) and client_secret (App Secret) in config
    """

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._client_id: str      = config.client_id
        self._client_secret: str  = config.client_secret
        self._allowlist: list[str] = list(config.allowlist)
        self._access_token: str   = ""
        self._running = False
        self._token_task: asyncio.Task | None = None
        self._ws_task: asyncio.Task | None    = None

    async def start(self) -> None:
        self._running = True
        self._token_task = asyncio.create_task(self._token_loop(), name="dingtalk-token")
        await asyncio.sleep(1)  # give token loop time for first fetch
        self._ws_task = asyncio.create_task(self._stream_loop(), name="dingtalk-stream")
        log.info("[dingtalk] connector started (stream=%s)", self._stream)

    async def stop(self) -> None:
        self._running = False
        tasks = [t for t in (self._token_task, self._ws_task) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("[dingtalk] connector stopped")

    # ------------------------------------------------------------------
    # Token management  (tokens are valid 7200 s; refresh every ~115 min)
    # ------------------------------------------------------------------

    async def _token_loop(self) -> None:
        while self._running:
            try:
                self._access_token = await asyncio.to_thread(self._fetch_token)
                log.debug("[dingtalk] access token refreshed")
            except Exception as exc:
                log.error("[dingtalk] token fetch failed: %s", exc)
            try:
                await asyncio.sleep(6_900)
            except asyncio.CancelledError:
                break

    def _fetch_token(self) -> str:
        payload = json.dumps({
            "appKey": self._client_id,
            "appSecret": self._client_secret,
        }).encode()
        req = urllib.request.Request(
            f"{API_BASE}/v1.0/oauth2/accessToken",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())["accessToken"]

    # ------------------------------------------------------------------
    # Stream WebSocket loop
    # ------------------------------------------------------------------

    async def _stream_loop(self) -> None:
        import websockets  # type: ignore[import-untyped]

        backoff = 5
        while self._running:
            try:
                ws_url = await asyncio.to_thread(self._open_stream)
                async with websockets.connect(ws_url) as ws:
                    backoff = 5
                    async for raw in ws:
                        await self._on_event(ws, json.loads(raw))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[dingtalk] stream error: %s — reconnect in %ds", exc, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    def _open_stream(self) -> str:
        """Register subscriptions and get a WebSocket endpoint URL."""
        payload = json.dumps({
            "clientId":     self._client_id,
            "clientSecret": self._client_secret,
            "subscriptions": [
                {"type": "EVENT", "topic": "/v1.0/im/bot/messages/get"},
                {"type": "EVENT", "topic": "/v1.0/im/bot/groupMessages/get"},
            ],
            "ua": "ghostclaw/1.0",
        }).encode()
        req = urllib.request.Request(
            f"{API_BASE}/v1.0/gateway/connections/open",
            data=payload,
            headers={
                "Content-Type":                   "application/json",
                "x-acs-dingtalk-access-token":    self._access_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            data = json.loads(resp.read())
        return data["endpoint"]

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _on_event(self, ws, msg: dict) -> None:
        headers = msg.get("headers", {})

        # Always ACK immediately (DingTalk requires it)
        await ws.send(json.dumps({
            "code":    200,
            "headers": headers,
            "message": "OK",
            "data":    "",
        }))

        if msg.get("type") != "EVENT":
            return
        topic = headers.get("topic", "")
        if "messages" not in topic:
            return

        try:
            data = json.loads(msg.get("data", "{}"))
        except Exception:
            return

        sender_id       = data.get("senderId", "")
        conversation_id = data.get("conversationId", "")
        text_obj        = data.get("text", {})
        text: str       = (text_obj.get("content") if isinstance(text_obj, dict) else "").strip()

        if not text:
            return
        if self._allowlist and sender_id not in self._allowlist:
            return

        asyncio.create_task(
            self._handle_message(conversation_id, text),
            name=f"dingtalk-msg-{conversation_id}",
        )

    # ------------------------------------------------------------------
    # Streaming helpers (DingTalk doesn't support edit-in-place)
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        # No streaming edit support — defer all output to final send
        return handle or {"pending": True}

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        text = text or "(无响应)"
        await asyncio.to_thread(self._send_text, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level API helpers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _send_text(self, conversation_id: str, text: str) -> None:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        payload = json.dumps({
            "robotCode":          self._client_id,
            "openConversationId": conversation_id,
            "msgParamList": [{
                "msgKey":   "sampleText",
                "msgParam": json.dumps({"content": text}),
            }],
        }, ensure_ascii=False).encode()
        req = urllib.request.Request(
            f"{API_BASE}/v1.0/im/bot/groupMessages/send",
            data=payload,
            headers={
                "Content-Type":                "application/json",
                "x-acs-dingtalk-access-token": self._access_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            json.loads(resp.read())
