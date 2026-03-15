"""Slack Socket Mode connector — WebSocket-based, zero extra deps beyond websockets."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from ghostclaw.connectors.base import Connector, log
from ghostclaw.util.ssl_context import make_ssl_context

API_BASE    = "https://slack.com/api"
MAX_MSG_LEN = 4000  # Slack's practical per-block text limit


class SlackConnector(Connector):
    """
    Connects to Slack via Socket Mode (WebSocket).
    Requires a Bot Token (xoxb-…) and an App-Level Token (xapp-…) with
    connections:write scope. Enable Socket Mode in your Slack App settings.
    """

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._bot_token: str      = config.bot_token   # xoxb-…
        self._app_token: str      = config.app_token   # xapp-…
        self._allowlist: list[str] = list(config.allowlist)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._socket_loop(), name="slack-socket")
        log.info("[slack] connector started (stream=%s)", self._stream)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        log.info("[slack] connector stopped")

    # ------------------------------------------------------------------
    # Socket Mode loop
    # ------------------------------------------------------------------

    async def _socket_loop(self) -> None:
        import websockets  # type: ignore[import-untyped]

        backoff = 5
        while self._running:
            try:
                wss_url = await asyncio.to_thread(self._open_connection)
                async with websockets.connect(wss_url) as ws:
                    backoff = 5
                    ping_task = asyncio.create_task(
                        self._ping_loop(ws), name="slack-ping"
                    )
                    try:
                        async for raw in ws:
                            await self._on_event(ws, json.loads(raw))
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[slack] socket error: %s — reconnect in %ds", exc, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    async def _ping_loop(self, ws) -> None:
        """Send periodic pings to keep the Socket Mode connection alive."""
        try:
            while True:
                await asyncio.sleep(30)
                await ws.send(json.dumps({"type": "ping"}))
        except (asyncio.CancelledError, Exception):
            pass

    def _open_connection(self) -> str:
        """Call apps.connections.open to obtain a Socket Mode WebSocket URL."""
        req = urllib.request.Request(
            f"{API_BASE}/apps.connections.open",
            data=b"",
            headers={
                "Authorization":  f"Bearer {self._app_token}",
                "Content-Type":   "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            data = json.loads(resp.read())
        if not data.get("ok"):
            raise RuntimeError(f"Slack connections.open failed: {data.get('error')}")
        return data["url"]

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _on_event(self, ws, msg: dict) -> None:
        msg_type    = msg.get("type")
        envelope_id = msg.get("envelope_id")

        if msg_type == "hello":
            log.info("[slack] socket connected")
            return

        if msg_type == "disconnect":
            log.info("[slack] disconnect requested: %s", msg.get("reason", "unknown"))
            if envelope_id:
                await ws.send(json.dumps({"envelope_id": envelope_id}))
            return

        # ACK immediately — Slack requires acknowledgement within 3 seconds
        if envelope_id:
            await ws.send(json.dumps({"envelope_id": envelope_id}))

        if msg_type == "events_api":
            event = msg.get("payload", {}).get("event", {})
            await self._dispatch_event(event)

    async def _dispatch_event(self, event: dict) -> None:
        if event.get("type") != "message":
            return
        # Ignore bot messages, edits, deletions, etc.
        if event.get("subtype") or event.get("bot_id"):
            return

        channel = event.get("channel", "")
        text: str = (event.get("text") or "").strip()

        if self._allowlist and channel not in self._allowlist:
            return

        # Handle file attachments (Slack file_share subtype sends files in event.files)
        slack_files = event.get("files") or []
        attachment_lines: list[str] = []
        for f in slack_files:
            url = f.get("url_private_download") or f.get("url_private", "")
            filename = f.get("name") or f.get("title") or "attachment"
            if url:
                local_path = await asyncio.to_thread(
                    self._download_to_upload_dir, url, filename,
                    {"Authorization": f"Bearer {self._bot_token}"}
                )
                if local_path:
                    attachment_lines.append(f"- {filename} (local path: {local_path})")

        if attachment_lines:
            text = (text + "\n\n" if text else "") + "[Attached files]\n" + "\n".join(attachment_lines)

        if not text:
            return

        asyncio.create_task(
            self._handle_message(channel, text),
            name=f"slack-msg-{channel}",
        )

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    async def _stream_update(self, chat_id: str, text: str, handle: Any) -> Any:
        """Post or update a Slack message. handle = {"ts": str}"""
        if handle is None:
            ts = await asyncio.to_thread(self._post_message, chat_id, text)
            return {"ts": ts}
        try:
            await asyncio.to_thread(self._update_message, chat_id, handle["ts"], text)
        except Exception:
            pass
        return handle

    async def _send_final(self, chat_id: str, text: str, handle: Any) -> None:
        text = text or "(no response)"
        if handle is None:
            await asyncio.to_thread(self._post_message, chat_id, text)
        else:
            try:
                await asyncio.to_thread(self._update_message, chat_id, handle["ts"], text)
            except Exception:
                await asyncio.to_thread(self._post_message, chat_id, text)

    # ------------------------------------------------------------------
    # Low-level API helpers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _slack_api(self, method: str, **kwargs) -> dict:
        req = urllib.request.Request(
            f"{API_BASE}/{method}",
            data=json.dumps(kwargs).encode(),
            headers={
                "Authorization": f"Bearer {self._bot_token}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())

    def _post_message(self, channel: str, text: str) -> str:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        resp = self._slack_api("chat.postMessage", channel=channel, text=text)
        return resp.get("ts", "")

    def _update_message(self, channel: str, ts: str, text: str) -> None:
        if len(text) > MAX_MSG_LEN:
            text = text[:MAX_MSG_LEN - 1] + "…"
        self._slack_api("chat.update", channel=channel, ts=ts, text=text)
