"""Discord Bot connector — WebSocket gateway (zero extra deps beyond websockets)."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from hushclaw.connectors.base import Connector, log
from hushclaw.util.ssl_context import make_ssl_context

# Discord Gateway
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
API_BASE    = "https://discord.com/api/v10"

# Gateway intents: GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT
INTENTS = 1 | 512 | 4096 | 32768

MAX_MSG_LEN = 2000  # Discord hard character limit per message

# Gateway opcodes
OP_DISPATCH   = 0
OP_HEARTBEAT  = 1
OP_IDENTIFY   = 2
OP_RECONNECT  = 7
OP_INVALID    = 9
OP_HELLO      = 10
OP_HB_ACK    = 11


class DiscordConnector(Connector):
    """
    Connects to Discord Gateway via WebSocket.
    Responds to DMs unconditionally; guild messages require @mention by default.
    Zero extra deps — uses the `websockets` library already installed for the server.
    """

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._token: str              = config.bot_token
        self._allowlist: list[int]    = list(config.allowlist)
        self._guild_allowlist: list[int] = list(config.guild_allowlist)
        self._require_mention: bool   = config.require_mention
        self._running = False
        self._task: asyncio.Task | None = None
        self._bot_id: str = ""

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._gateway_loop(), name="discord-gateway")
        log.info("[discord] connector started (require_mention=%s, stream=%s)",
                 self._require_mention, self._stream)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        log.info("[discord] connector stopped")

    # ------------------------------------------------------------------
    # Gateway loop
    # ------------------------------------------------------------------

    async def _gateway_loop(self) -> None:
        import websockets  # type: ignore[import-untyped]

        backoff = 5
        while self._running:
            try:
                async with websockets.connect(GATEWAY_URL) as ws:
                    backoff = 5
                    hb_task: asyncio.Task | None = None
                    try:
                        async for raw in ws:
                            msg  = json.loads(raw)
                            op   = msg.get("op")
                            data = msg.get("d")
                            t    = msg.get("t")
                            s    = msg.get("s")

                            if op == OP_HELLO:
                                interval = data["heartbeat_interval"] / 1000
                                hb_task = asyncio.create_task(
                                    self._heartbeat(ws, interval), name="discord-hb"
                                )
                                await ws.send(json.dumps({
                                    "op": OP_IDENTIFY,
                                    "d": {
                                        "token": self._token,
                                        "intents": INTENTS,
                                        "properties": {
                                            "os": "linux",
                                            "browser": "hushclaw",
                                            "device": "hushclaw",
                                        },
                                    },
                                }))

                            elif op == OP_DISPATCH:
                                if t == "READY":
                                    user = data.get("user", {})
                                    self._bot_id = str(user.get("id", ""))
                                    log.info("[discord] logged in as %s", user.get("username"))
                                elif t == "MESSAGE_CREATE":
                                    await self._on_message(data)

                            elif op in (OP_RECONNECT, OP_INVALID):
                                log.info("[discord] gateway op=%d — reconnecting", op)
                                break
                    finally:
                        if hb_task:
                            hb_task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[discord] gateway error: %s — reconnect in %ds", exc, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    async def _heartbeat(self, ws, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": None}))
        except (asyncio.CancelledError, Exception):
            pass

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _on_message(self, data: dict) -> None:
        author = data.get("author", {})
        if author.get("bot"):
            return  # ignore bot messages

        user_id     = int(author.get("id", 0))
        channel_id  = str(data.get("channel_id", ""))
        guild_id_s  = data.get("guild_id")
        guild_id    = int(guild_id_s) if guild_id_s else 0
        content: str = data.get("content", "").strip()

        # User allowlist
        if self._allowlist and user_id not in self._allowlist:
            return
        # Guild allowlist
        if guild_id and self._guild_allowlist and guild_id not in self._guild_allowlist:
            return

        # Guild (non-DM) messages: optionally require @mention
        if guild_id:
            mention_plain  = f"<@{self._bot_id}>"
            mention_nick   = f"<@!{self._bot_id}>"
            has_mention    = mention_plain in content or mention_nick in content
            if self._require_mention and self._bot_id and not has_mention:
                return
            # Strip the mention from the content so the agent doesn't see it
            content = content.replace(mention_plain, "").replace(mention_nick, "").strip()

        # Handle file attachments
        msg_attachments = data.get("attachments") or []
        attachment_lines: list[str] = []
        for att in msg_attachments:
            url = att.get("url", "")
            filename = att.get("filename", "attachment")
            if url:
                local_path = await asyncio.to_thread(
                    self._download_to_upload_dir, url, filename
                )
                if local_path:
                    attachment_lines.append(f"- {filename} (local path: {local_path})")

        if attachment_lines:
            content = (content + "\n\n" if content else "") + "[Attached files]\n" + "\n".join(attachment_lines)

        if not content:
            return

        asyncio.create_task(
            self._handle_message(channel_id, content),
            name=f"discord-msg-{channel_id}",
        )

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        """Send or edit a Discord message. handle = {"message_id": str}"""
        if handle is None:
            msg_id = await asyncio.to_thread(self._send_message, chat_id, text)
            return {"message_id": msg_id}
        try:
            await asyncio.to_thread(self._edit_message, chat_id, handle["message_id"], text)
        except Exception:
            pass
        return handle

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        text = text or "(no response)"
        if handle is None:
            await asyncio.to_thread(self._send_message, chat_id, text)
        else:
            try:
                await asyncio.to_thread(self._edit_message, chat_id, handle["message_id"], text)
            except Exception:
                await asyncio.to_thread(self._send_message, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level API helpers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _api(self, method: str, path: str, **kwargs) -> dict:
        url  = f"{API_BASE}{path}"
        data = json.dumps(kwargs).encode() if kwargs else None
        req  = urllib.request.Request(
            url, data=data,
            headers={
                "Authorization": f"Bot {self._token}",
                "Content-Type":  "application/json",
                "User-Agent":    "HushClaw (https://github.com/CNTWDev/hushclaw, 1.0)",
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())

    def _send_message(self, channel_id: str, text: str) -> str:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        result = self._api("POST", f"/channels/{channel_id}/messages", content=text)
        return result["id"]

    def _edit_message(self, channel_id: str, message_id: str, text: str) -> None:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        self._api("PATCH", f"/channels/{channel_id}/messages/{message_id}", content=text)
