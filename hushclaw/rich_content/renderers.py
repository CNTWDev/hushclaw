"""Channel capability metadata and outbound rendering helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json
import re

from .models import RichContentDocument, parse_rich_content


@dataclass(frozen=True)
class ChannelCapabilities:
    inbound_text: bool = True
    threaded_reply: bool = False
    rich_text: bool = False
    attachments: bool = False
    voice: bool = False
    quote_context: bool = False
    delivery_state: bool = False
    approval_required_actions: bool = False
    background_wakeups: bool = False
    max_message_len: int = 0


@dataclass(frozen=True)
class ChannelRenderResult:
    channel: str
    format: str
    body: str
    plain_text: str
    document: RichContentDocument
    metadata: dict[str, Any] = field(default_factory=dict)


CHANNEL_CAPABILITIES: dict[str, ChannelCapabilities] = {
    "telegram": ChannelCapabilities(
        threaded_reply=True,
        rich_text=True,
        attachments=True,
        quote_context=True,
        background_wakeups=True,
        max_message_len=4096,
    ),
    "feishu": ChannelCapabilities(
        threaded_reply=False,
        rich_text=True,
        attachments=False,
        background_wakeups=True,
    ),
    "discord": ChannelCapabilities(
        threaded_reply=True,
        rich_text=True,
        attachments=True,
        quote_context=True,
        background_wakeups=True,
        max_message_len=2000,
    ),
    "slack": ChannelCapabilities(
        threaded_reply=True,
        rich_text=True,
        attachments=True,
        quote_context=True,
        delivery_state=True,
        background_wakeups=True,
        max_message_len=4000,
    ),
    "dingtalk": ChannelCapabilities(
        rich_text=True,
        background_wakeups=True,
    ),
    "wecom": ChannelCapabilities(
        rich_text=True,
        background_wakeups=True,
    ),
    "whatsapp": ChannelCapabilities(
        threaded_reply=True,
        rich_text=False,
        attachments=True,
        quote_context=True,
        delivery_state=True,
        background_wakeups=True,
    ),
}

_CHANNEL_FORMAT_HINTS: dict[str, str] = {
    "telegram": "Format for Telegram: prefer Telegram-safe HTML with bold, italic, links, and fenced code blocks when useful. Split very long replies across messages.",
    "feishu": "Format for Feishu: prefer compact post cards for structured output; fall back to concise plain text when structure would be noisy.",
    "discord": "Format for Discord: prefer Discord markdown and keep replies within the platform's practical message limits.",
    "slack": "Format for Slack: prefer Slack mrkdwn blocks for readable structure, links, and code.",
    "dingtalk": "Format for DingTalk: prefer sampleMarkdown for structured replies; otherwise send concise plain text.",
    "wecom": "Format for WeCom: prefer WeCom markdown for concise structured replies; fall back to plain text when necessary.",
    "whatsapp": "Format for WhatsApp: plain text first. Keep replies concise and split long responses.",
}

CHANNEL_RENDER_MODES: dict[str, tuple[dict[str, str], ...]] = {
    "telegram": (
        {"value": "telegram_html", "label": "Telegram HTML", "description": "Native Telegram HTML formatting for bold, italic, code, and links."},
        {"value": "plain", "label": "Plain text", "description": "No rich formatting; safest fallback for strict clients."},
    ),
    "feishu": (
        {"value": "feishu_post", "label": "Rich post", "description": "Structured Feishu post card with grouped paragraphs and code fences."},
        {"value": "plain", "label": "Plain text", "description": "Simple Feishu text message with no card formatting."},
    ),
    "discord": (
        {"value": "discord_markdown", "label": "Discord Markdown", "description": "Standard Discord markdown rendering for headings, lists, and code."},
        {"value": "plain", "label": "Plain text", "description": "Send a plain text message with formatting stripped."},
    ),
    "slack": (
        {"value": "slack_mrkdwn", "label": "Slack mrkdwn", "description": "Slack section blocks using mrkdwn formatting."},
        {"value": "plain", "label": "Plain text", "description": "Send a plain Slack message without mrkdwn blocks."},
    ),
    "dingtalk": (
        {"value": "sample_markdown", "label": "sampleMarkdown", "description": "DingTalk markdown message with title plus formatted body."},
        {"value": "plain", "label": "Plain text", "description": "Send a DingTalk sampleText message."},
    ),
    "wecom": (
        {"value": "wecom_markdown", "label": "WeCom Markdown", "description": "WeCom markdown message type with links and emphasis."},
        {"value": "plain", "label": "Plain text", "description": "Send a WeCom text message."},
    ),
    "whatsapp": (
        {"value": "plain", "label": "Plain text", "description": "WhatsApp-style concise plain text."},
    ),
}


_RE_FENCE = re.compile(r"```(\w*)\n?([\s\S]*?)```")
_RE_INLCODE = re.compile(r"`([^`\n]+)`")
_RE_HEADER = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_RE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!\w)_([^_\n]+?)_(?!\w)", re.DOTALL)
_RE_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_RE_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")

_RE_STRIP_FENCE = re.compile(r"```\w*\n?([\s\S]*?)```")
_RE_STRIP_INLCODE = re.compile(r"`([^`\n]+)`")
_RE_STRIP_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_RE_STRIP_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_RE_STRIP_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!\w)_([^_\n]+?)_(?!\w)")
_RE_STRIP_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_RE_STRIP_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_markdown(text: str) -> str:
    text = _RE_STRIP_FENCE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_INLCODE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_HEADER.sub("", text)
    text = _RE_STRIP_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _RE_STRIP_ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _RE_STRIP_STRIKE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    return text


def markdown_to_telegram_html(text: str) -> str:
    placeholders: dict[str, str] = {}
    counter = [0]

    def store(html: str) -> str:
        key = f"\x00{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = html
        return key

    def fence_sub(match: re.Match) -> str:
        lang = match.group(1)
        code = _html_escape(match.group(2).strip())
        if lang:
            return store(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return store(f"<pre>{code}</pre>")

    text = _RE_FENCE.sub(fence_sub, text)
    text = _RE_INLCODE.sub(lambda m: store(f"<code>{_html_escape(m.group(1))}</code>"), text)
    text = _html_escape(text)
    text = _RE_HEADER.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _RE_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _RE_ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _RE_STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _RE_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def _inline_telegram_html(text: str) -> str:
    return markdown_to_telegram_html(text).replace("<pre>", "").replace("</pre>", "")


def _telegram_html_from_document(document: RichContentDocument) -> str:
    chunks: list[str] = []
    for block in document.blocks:
        if block.kind == "heading":
            chunks.append(f"<b>{_html_escape(block.text)}</b>")
            continue
        if block.kind == "paragraph":
            chunks.append(_inline_telegram_html(block.text))
            continue
        if block.kind == "list":
            chunks.extend([f"• {_inline_telegram_html(item)}" for item in block.items])
            continue
        if block.kind == "quote":
            for line in block.text.splitlines():
                chunks.append(f"&gt; {_inline_telegram_html(line)}")
            continue
        if block.kind == "code_block":
            code = _html_escape(block.text.strip())
            if block.lang:
                chunks.append(f'<pre><code class="language-{block.lang}">{code}</code></pre>')
            else:
                chunks.append(f"<pre>{code}</pre>")
            continue
        if block.text:
            chunks.append(_inline_telegram_html(block.text))
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def get_channel_default_render_mode(channel: str) -> str:
    channel_id = (channel or "").strip().lower()
    modes = CHANNEL_RENDER_MODES.get(channel_id)
    if not modes:
        return "plain"
    return str(modes[0].get("value") or "plain")


def get_channel_render_mode_options(channel: str) -> tuple[dict[str, str], ...]:
    return CHANNEL_RENDER_MODES.get((channel or "").strip().lower(), ())


def get_channel_render_mode_label(channel: str, render_mode: str) -> str:
    normalized = normalize_channel_render_mode(channel, render_mode)
    for option in get_channel_render_mode_options(channel):
        if option.get("value") == normalized:
            return str(option.get("label") or normalized)
    return normalized.replace("_", " ").title()


def normalize_channel_render_mode(
    channel: str,
    render_mode: str | None,
    *,
    legacy_markdown: bool | None = None,
) -> str:
    channel_id = (channel or "").strip().lower()
    mode = str(render_mode or "").strip().lower()
    allowed = {str(option.get("value") or "").strip().lower() for option in get_channel_render_mode_options(channel_id)}
    if mode and mode in allowed:
        return mode
    if legacy_markdown is not None:
        return get_channel_default_render_mode(channel_id) if legacy_markdown else "plain"
    return get_channel_default_render_mode(channel_id)


def _feishu_post_title(document: RichContentDocument) -> str:
    for block in document.blocks:
        if block.kind == "heading" and block.text.strip():
            return block.text.strip()[:60]
    for line in (document.source_text or "").splitlines():
        stripped = strip_markdown(line).strip()
        if stripped:
            return stripped[:60]
    return "Reply"


def _feishu_post_row(text: str) -> list[dict[str, str]]:
    return [{"tag": "text", "text": text}]


def _render_feishu_post(document: RichContentDocument) -> str:
    rows: list[list[dict[str, str]]] = []
    for block in document.blocks:
        if block.kind == "heading":
            rows.append(_feishu_post_row(f"{'#' * max(1, block.level)} {block.text}".strip()))
            continue
        if block.kind == "paragraph":
            rows.append(_feishu_post_row(block.text))
            continue
        if block.kind == "list":
            for item in block.items:
                rows.append(_feishu_post_row(f"• {item}"))
            continue
        if block.kind == "quote":
            for line in block.text.splitlines():
                rows.append(_feishu_post_row(f"› {line}".strip()))
            continue
        if block.kind == "code_block":
            lang = block.lang.strip()
            fenced = f"```{lang}\n{block.text}\n```" if lang else f"```\n{block.text}\n```"
            rows.append(_feishu_post_row(fenced))
            continue
        if block.text:
            rows.append(_feishu_post_row(block.text))
    if not rows:
        rows = [_feishu_post_row(" ")]
    payload = {
        "zh_cn": {
            "title": _feishu_post_title(document),
            "content": rows,
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def render_channel_message(
    channel: str,
    content: str | RichContentDocument,
    *,
    render_mode: str | None = None,
    prefer_rich: bool = True,
) -> ChannelRenderResult:
    document = content if isinstance(content, RichContentDocument) else parse_rich_content(content)
    source = document.source_text or ""
    plain_text = strip_markdown(source)
    channel_id = (channel or "").strip().lower()
    mode = normalize_channel_render_mode(channel_id, render_mode, legacy_markdown=prefer_rich)

    if channel_id == "telegram":
        if mode == "telegram_html":
            return ChannelRenderResult(
                channel=channel_id,
                format="telegram_html",
                body=_telegram_html_from_document(document),
                plain_text=plain_text,
                document=document,
                metadata={"parse_mode": "HTML", "disable_web_page_preview": False},
            )
        return ChannelRenderResult(
            channel=channel_id,
            format="plain",
            body=plain_text,
            plain_text=plain_text,
            document=document,
            metadata={"parse_mode": "", "disable_web_page_preview": False},
        )

    if channel_id == "feishu":
        if mode == "feishu_post":
            return ChannelRenderResult(
                channel=channel_id,
                format="feishu_post",
                body=_render_feishu_post(document),
                plain_text=plain_text,
                document=document,
                metadata={"msg_type": "post"},
            )
        return ChannelRenderResult(
            channel=channel_id,
            format="plain",
            body=plain_text,
            plain_text=plain_text,
            document=document,
            metadata={"msg_type": "text"},
        )

    if channel_id == "slack":
        if mode == "slack_mrkdwn":
            return ChannelRenderResult(channel=channel_id, format="slack_mrkdwn", body=source, plain_text=plain_text, document=document, metadata={"msg_type": "mrkdwn"})
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document, metadata={"msg_type": "text"})

    if channel_id == "discord":
        if mode == "discord_markdown":
            return ChannelRenderResult(channel=channel_id, format="discord_markdown", body=source, plain_text=plain_text, document=document, metadata={"msg_type": "markdown"})
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document, metadata={"msg_type": "text"})

    if channel_id == "dingtalk":
        if mode == "sample_markdown":
            return ChannelRenderResult(channel=channel_id, format="sample_markdown", body=source, plain_text=plain_text, document=document, metadata={"msg_type": "sampleMarkdown"})
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document, metadata={"msg_type": "sampleText"})

    if channel_id == "wecom":
        if mode == "wecom_markdown":
            return ChannelRenderResult(channel=channel_id, format="wecom_markdown", body=source, plain_text=plain_text, document=document, metadata={"msg_type": "markdown"})
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document, metadata={"msg_type": "text"})

    if channel_id == "whatsapp":
        return ChannelRenderResult(
            channel=channel_id,
            format="plain",
            body=plain_text,
            plain_text=plain_text,
            document=document,
            metadata={"msg_type": "text"},
        )

    return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document, metadata={"msg_type": "text"})


def build_channel_prompt_hint(channel: str, render_mode: str | None = None) -> str:
    channel_id = (channel or "").strip().lower()
    caps = CHANNEL_CAPABILITIES.get(channel_id)
    format_hint = _CHANNEL_FORMAT_HINTS.get(channel_id)
    if not caps or not format_hint:
        return ""
    mode = normalize_channel_render_mode(channel_id, render_mode)
    lines = [f"## Channel: {channel_id.replace('_', ' ').title()}", format_hint]
    lines.append(f"Current render mode: {get_channel_render_mode_label(channel_id, mode)}.")
    traits: list[str] = []
    if caps.threaded_reply:
        traits.append("supports threaded replies")
    if caps.rich_text:
        traits.append("supports rich formatted output")
    else:
        traits.append("treat as plain-text-first")
    if caps.attachments:
        traits.append("supports attachments")
    if caps.quote_context:
        traits.append("can preserve quoted or replied-to context")
    if caps.max_message_len:
        traits.append(f"soft message budget ≈ {caps.max_message_len} chars")
    if traits:
        lines.append("Channel capabilities: " + "; ".join(traits) + ".")
    lines.append("Attach files through file/artifact tools; never paste large binary content inline.")
    return "\n".join(lines)
