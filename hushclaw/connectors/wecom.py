"""WeCom (企业微信) connector — HTTP callback webhook."""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from hushclaw.connectors.base import Connector, log
from hushclaw.util.ssl_context import make_ssl_context

API_BASE    = "https://qyapi.weixin.qq.com/cgi-bin"
MAX_MSG_LEN = 2048  # WeCom text message limit


class WeChatWorkConnector(Connector):
    """
    Receives WeCom (企业微信) messages via HTTP callback webhook.

    Setup:
    1. Create an app in WeCom Admin Console → App Management
    2. In "Receive Messages" settings, set callback URL to:
       http(s)://your-server/webhook/wecom
    3. Set Token and EncodingAESKey (or leave AES key empty for plaintext)
    4. Set corp_id, corp_secret, agent_id, and token in HushClaw config

    The server must be publicly accessible for WeCom to deliver callbacks.
    For local development, use a tunnel like ngrok: ngrok http 8765
    Then set your server's public URL in WeCom's callback settings.
    """

    WEBHOOK_PATH = "wecom"  # registered as /webhook/wecom

    def __init__(self, gateway, config, webhook_registry: dict) -> None:
        super().__init__(gateway, config)
        self._corp_id: str          = config.corp_id
        self._corp_secret: str      = config.corp_secret
        self._agent_id: int         = config.agent_id
        self._token: str            = config.token
        self._aes_key: str          = config.encoding_aes_key
        self._allowlist: list[str]  = list(config.allowlist)
        self._access_token: str     = ""
        self._running               = False
        self._token_task: asyncio.Task | None = None
        self._webhook_registry      = webhook_registry

    async def start(self) -> None:
        self._running = True
        self._token_task = asyncio.create_task(self._token_loop(), name="wecom-token")
        self._webhook_registry[self.WEBHOOK_PATH] = self._handle_webhook
        log.info("[wecom] connector started — webhook endpoint: POST /webhook/wecom")

    async def stop(self) -> None:
        self._running = False
        self._webhook_registry.pop(self.WEBHOOK_PATH, None)
        if self._token_task:
            self._token_task.cancel()
            await asyncio.gather(self._token_task, return_exceptions=True)
        log.info("[wecom] connector stopped")

    # ------------------------------------------------------------------
    # Token management (tokens valid 7200 s; refresh every ~115 min)
    # ------------------------------------------------------------------

    async def _token_loop(self) -> None:
        while self._running:
            try:
                self._access_token = await asyncio.to_thread(self._fetch_token)
                log.debug("[wecom] access token refreshed")
            except Exception as exc:
                log.error("[wecom] token fetch failed: %s", exc)
            try:
                await asyncio.sleep(6_900)
            except asyncio.CancelledError:
                break

    def _fetch_token(self) -> str:
        secret = urllib.parse.quote(self._corp_secret)
        url    = f"{API_BASE}/gettoken?corpid={self._corp_id}&corpsecret={secret}"
        with urllib.request.urlopen(
            urllib.request.Request(url), timeout=10, context=make_ssl_context()
        ) as resp:
            data = json.loads(resp.read())
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"WeCom gettoken error: {data}")
        return data["access_token"]

    # ------------------------------------------------------------------
    # Webhook handler (called by server._http_handler)
    # ------------------------------------------------------------------

    async def _handle_webhook(
        self, path: str, query: str, body: bytes
    ) -> tuple[int, bytes]:
        """
        Process an incoming WeCom callback.
        Returns (http_status, response_body).
        """
        params      = urllib.parse.parse_qs(query)
        echostr     = (params.get("echostr") or [""])[0]
        timestamp   = (params.get("timestamp") or [""])[0]
        nonce       = (params.get("nonce") or [""])[0]
        msg_sig     = (params.get("msg_signature") or [""])[0]

        # ── URL verification (GET-like challenge during initial setup) ──
        if echostr and not body:
            if not self._verify_signature(msg_sig, timestamp, nonce, echostr):
                log.warning("[wecom] webhook verification: bad signature")
                return 403, b"forbidden"
            return 200, echostr.encode()

        # ── Incoming message (POST body) ────────────────────────────────
        if not body:
            return 200, b""

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            log.warning("[wecom] malformed XML: %s", exc)
            return 400, b"invalid xml"

        msg_type  = root.findtext("MsgType", "")
        from_user = root.findtext("FromUserName", "")
        content   = root.findtext("Content", "").strip()

        if msg_type != "text" or not content:
            return 200, b""
        if self._allowlist and from_user not in self._allowlist:
            return 200, b""

        asyncio.create_task(
            self._handle_message(from_user, content),
            name=f"wecom-msg-{from_user}",
        )
        return 200, b""

    def _verify_signature(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> bool:
        """SHA1 signature check for WeCom webhook verification."""
        parts = sorted([self._token, timestamp, nonce, echostr])
        digest = hashlib.sha1("".join(parts).encode()).hexdigest()
        return digest == msg_signature

    # ------------------------------------------------------------------
    # Streaming helpers (WeCom has no edit-in-place)
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        return handle or {"pending": True}

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        text = text or "(无响应)"
        await asyncio.to_thread(self._send_text, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level API helpers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _send_text(self, to_user: str, text: str) -> None:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        payload = json.dumps({
            "touser":   to_user,
            "msgtype":  "text",
            "agentid":  self._agent_id,
            "text":     {"content": text},
        }, ensure_ascii=False).encode()
        url = f"{API_BASE}/message/send?access_token={self._access_token}"
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            result = json.loads(resp.read())
        if result.get("errcode", 0) != 0:
            log.error("[wecom] send_text error: %s", result)
