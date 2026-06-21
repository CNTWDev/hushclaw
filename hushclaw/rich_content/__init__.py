"""Structured rich-content model and channel renderers."""
from .models import RichBlock, RichContentDocument, parse_rich_content
from .renderers import (
    ChannelCapabilities,
    ChannelRenderResult,
    CHANNEL_CAPABILITIES,
    CHANNEL_RENDER_MODES,
    build_channel_prompt_hint,
    get_channel_default_render_mode,
    get_channel_render_mode_label,
    get_channel_render_mode_options,
    normalize_channel_render_mode,
    render_channel_message,
)

__all__ = [
    "CHANNEL_CAPABILITIES",
    "CHANNEL_RENDER_MODES",
    "ChannelCapabilities",
    "ChannelRenderResult",
    "RichBlock",
    "RichContentDocument",
    "build_channel_prompt_hint",
    "get_channel_default_render_mode",
    "get_channel_render_mode_label",
    "get_channel_render_mode_options",
    "normalize_channel_render_mode",
    "parse_rich_content",
    "render_channel_message",
]
