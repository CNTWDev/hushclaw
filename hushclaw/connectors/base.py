"""Connector ABC — base class for all external platform connectors."""
from __future__ import annotations

import asyncio
import re
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from hushclaw.rich_content import (
    CHANNEL_CAPABILITIES,
    ChannelRenderResult,
    get_channel_default_render_mode,
    normalize_channel_render_mode,
    parse_rich_content,
    render_channel_message,
)
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context
from hushclaw.os_api import AgentOSService
from hushclaw.os_contracts import AgentOSMessageRequest, ConversationAddress, ConversationBinding

log = get_logger("connectors")


class Connector(ABC):
    """Abstract base for Telegram, Feishu, and future platform connectors."""

    def __init__(self, gateway, config) -> None:
        self._gateway = gateway
        self._agent: str = config.agent
        self._workspace: str = getattr(config, "workspace", "") or ""
        self._channel_id: str = getattr(self, "CHANNEL_ID", self.__class__.__name__.replace("Connector", "").lower())
        self._render_mode: str = normalize_channel_render_mode(
            self._channel_id,
            getattr(config, "render_mode", ""),
            legacy_markdown=getattr(config, "markdown", None),
        ) or get_channel_default_render_mode(self._channel_id)
        # Subclasses set this True after a successful connection is established
        self._running: bool = False

    @property
    def connected(self) -> bool:
        """True if this connector has been started and is actively running."""
        return self._running

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def channel_capabilities(self):
        return CHANNEL_CAPABILITIES.get(self._channel_id)

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def _handle_message(self, chat_id: str, text: str) -> None:
        """Route an incoming message through the Gateway and deliver the reply."""
        os_api = getattr(self._gateway, "_os_api", None)
        if not isinstance(os_api, AgentOSService):
            raise RuntimeError("Connector requires an AgentOSService-bound gateway")
        address = ConversationAddress(provider=self._channel_id, conversation_id=str(chat_id))
        binding = os_api.get_conversation_binding(address)
        session_id = binding.session_id if binding is not None else ""
        if not session_id:
            session_id = make_id("c-")
        if binding is None:
            os_api.bind_conversation(
                ConversationBinding(
                    address=address,
                    session_id=session_id,
                    workspace=self._workspace,
                    agent=self._agent,
                )
            )
        full_text = ""
        client_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log.info(
            "[connector] inbound chat=%s session=%s agent=%s workspace=%r client_now=%s text=%r",
            chat_id,
            session_id[:12],
            self._agent,
            self._workspace or None,
            client_now,
            text[:120],
        )
        try:
            stream = os_api.stream_message(
                AgentOSMessageRequest(
                    agent=self._agent,
                    text=text,
                    session_id=session_id,
                    workspace=self._workspace,
                    client_now=client_now,
                    source_channel=f"connector:{self._channel_id}",
                    principal_id=f"connector:{self.__class__.__name__}:{chat_id}",
                    auth_context={"chat_id": chat_id},
                )
            )
            async for event in stream:
                if event.get("type") == "chunk":
                    full_text += event.get("text", "")
                elif event.get("type") == "done":
                    full_text = event.get("text", full_text)
        except Exception as exc:
            log.error("[connector] event_stream error for chat %s: %s", chat_id, exc)
            full_text = full_text or f"(error: {exc})"
        await self._send_reply(chat_id, full_text or "(no response)")

    def _download_to_upload_dir(self, url: str, filename: str,
                                extra_headers: dict | None = None) -> str | None:
        """Download a file from a platform URL and save to upload_dir. Returns local path or None."""
        try:
            upload_dir: Path | None = getattr(
                self._gateway._base_agent.config.server, "upload_dir", None
            )
            if upload_dir is None:
                return None
            upload_dir = Path(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)

            safe_name = re.sub(r"[^\w.\-]", "_", filename)[:128] or "attachment"
            file_id = uuid4().hex[:12]
            dest = upload_dir / f"{file_id}_{safe_name}"

            headers = {"User-Agent": "HushClaw/1.0"}
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=make_ssl_context()) as resp:
                dest.write_bytes(resp.read())
            log.info("[connector] downloaded attachment: %s → %s", filename, dest)
            return str(dest)
        except Exception as exc:
            log.warning("[connector] failed to download attachment %s: %s", filename, exc)
            return None

    def _build_reply_document(self, text: str):
        return parse_rich_content(text or "")

    def _render_reply(self, text: str) -> ChannelRenderResult:
        return render_channel_message(
            self._channel_id,
            self._build_reply_document(text or ""),
            render_mode=self._render_mode,
        )

    @abstractmethod
    async def _send_reply(self, chat_id: str, text: str) -> None: ...
