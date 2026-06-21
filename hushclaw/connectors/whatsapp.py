"""WhatsApp connector via Twilio webhook + REST API."""
from __future__ import annotations

import asyncio
import base64
import json
import urllib.parse
import urllib.request

from hushclaw.connectors.base import Connector, log
from hushclaw.util.ssl_context import make_ssl_context


class WhatsAppConnector(Connector):
    """Inbound/outbound WhatsApp connector backed by Twilio's WhatsApp APIs."""

    WEBHOOK_PATH = "whatsapp"
    CHANNEL_ID = "whatsapp"

    def __init__(self, gateway, config, webhook_registry: dict) -> None:
        super().__init__(gateway, config)
        self._account_sid: str = config.account_sid
        self._auth_token: str = config.auth_token
        self._from_number: str = config.from_number
        self._allowlist: list[str] = [str(v).strip() for v in list(config.allowlist) if str(v).strip()]
        self._webhook_registry = webhook_registry

    async def start(self) -> None:
        self._running = True
        self._webhook_registry[self.WEBHOOK_PATH] = self._handle_webhook
        log.info("[whatsapp] connector started — webhook endpoint: POST /webhook/whatsapp")

    async def stop(self) -> None:
        self._running = False
        self._webhook_registry.pop(self.WEBHOOK_PATH, None)
        log.info("[whatsapp] connector stopped")

    async def _handle_webhook(self, path: str, query: str, body: bytes) -> tuple[int, bytes]:
        if not body:
            return 200, b""
        data = urllib.parse.parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        from_number = str((data.get("From") or [""])[0]).strip()
        text = str((data.get("Body") or [""])[0]).strip()
        if self._allowlist and from_number not in self._allowlist:
            return 200, b""

        attachment_lines: list[str] = []
        try:
            media_count = max(0, int((data.get("NumMedia") or ["0"])[0] or "0"))
        except ValueError:
            media_count = 0
        for idx in range(media_count):
            media_url = str((data.get(f"MediaUrl{idx}") or [""])[0]).strip()
            content_type = str((data.get(f"MediaContentType{idx}") or [""])[0]).strip()
            if not media_url:
                continue
            ext = ""
            if "/" in content_type:
                ext = "." + content_type.split("/", 1)[1].split(";", 1)[0].strip()
            filename = f"whatsapp_media_{idx + 1}{ext or '.bin'}"
            local_path = await asyncio.to_thread(
                self._download_to_upload_dir,
                media_url,
                filename,
                {"Authorization": self._basic_auth_header()},
            )
            if local_path:
                attachment_lines.append(f"- {filename} (local path: {local_path})")

        if attachment_lines:
            text = (text + "\n\n" if text else "") + "[Attached files]\n" + "\n".join(attachment_lines)
        if text.strip():
            asyncio.create_task(
                self._handle_message(from_number, text),
                name=f"whatsapp-msg-{from_number}",
            )
        return 200, b""

    def _basic_auth_header(self) -> str:
        token = base64.b64encode(f"{self._account_sid}:{self._auth_token}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    async def _send_reply(self, chat_id: str, text: str) -> None:
        await asyncio.to_thread(self._send_message, chat_id, text or "(no response)")

    def _send_message(self, to_number: str, text: str) -> None:
        rendered = self._render_reply(text)
        payload = urllib.parse.urlencode({
            "To": to_number,
            "From": self._from_number,
            "Body": rendered.plain_text or rendered.body or "(no response)",
        }).encode("utf-8")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15, context=make_ssl_context()) as resp:
            result = json.loads(resp.read())
        if not result.get("sid"):
            raise RuntimeError(f"WhatsApp send failed: {result}")
