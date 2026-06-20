"""Structured rich-content model and channel renderers."""
from .models import RichBlock, RichContentDocument, parse_rich_content
from .renderers import (
    ChannelCapabilities,
    ChannelRenderResult,
    CHANNEL_CAPABILITIES,
    build_channel_prompt_hint,
    render_channel_message,
)

__all__ = [
    "CHANNEL_CAPABILITIES",
    "ChannelCapabilities",
    "ChannelRenderResult",
    "RichBlock",
    "RichContentDocument",
    "build_channel_prompt_hint",
    "parse_rich_content",
    "render_channel_message",
]
