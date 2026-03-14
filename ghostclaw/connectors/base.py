"""Connector ABC — base class for all external platform connectors."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger

log = get_logger("connectors")


class Connector(ABC):
    """Abstract base for Telegram, Feishu, and future platform connectors."""

    def __init__(self, gateway, config) -> None:
        self._gateway = gateway
        self._agent: str = config.agent
        self._stream: bool = getattr(config, "stream", True)
        # chat_id (str) → GhostClaw session_id
        self._sessions: dict[str, str] = {}

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def _handle_message(self, chat_id: str, text: str) -> None:
        """Route an incoming message through the Gateway and deliver the reply."""
        session_id = self._sessions.setdefault(chat_id, make_id("c-"))
        full_text = ""
        handle = None
        try:
            async for event in self._gateway.event_stream(self._agent, text, session_id):
                if event.get("type") == "chunk":
                    full_text += event.get("text", "")
                    if self._stream:
                        handle = await self._stream_update(chat_id, full_text, handle)
                elif event.get("type") == "done":
                    full_text = event.get("text", full_text)
        except Exception as exc:
            log.error("[connector] event_stream error for chat %s: %s", chat_id, exc)
            full_text = full_text or f"(错误：{exc})"
        await self._send_final(chat_id, full_text, handle)

    @abstractmethod
    async def _stream_update(self, chat_id: str, text: str, handle): ...

    @abstractmethod
    async def _send_final(self, chat_id: str, text: str, handle): ...
