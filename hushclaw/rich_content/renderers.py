"""Channel capability metadata and outbound rendering helpers."""
from __future__ import annotations

from dataclasses import dataclass
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
        rich_text=False,
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
    "telegram": "Format for Telegram: prefer Telegram-safe rich text with bold, italic, links, and fenced code blocks when useful. Split very long replies across messages.",
    "feishu": "Format for Feishu: prefer concise plain text. Keep structure simple and readable.",
    "discord": "Format for Discord: prefer Discord markdown and keep replies within the platform's practical message limits.",
    "slack": "Format for Slack: prefer Slack markdown blocks for readable structure, links, and code.",
    "dingtalk": "Format for DingTalk: prefer lightweight markdown when structure helps; otherwise send concise plain text.",
    "wecom": "Format for WeCom: prefer concise markdown for structured replies; fall back to plain text when necessary.",
    "whatsapp": "Format for WhatsApp: plain text first. Keep replies concise and split long responses.",
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


def render_channel_message(
    channel: str,
    content: str | RichContentDocument,
    *,
    prefer_rich: bool = True,
) -> ChannelRenderResult:
    document = content if isinstance(content, RichContentDocument) else parse_rich_content(content)
    source = document.source_text or ""
    plain_text = strip_markdown(source)
    channel_id = (channel or "").strip().lower()

    if channel_id == "telegram":
        if prefer_rich:
            return ChannelRenderResult(
                channel=channel_id,
                format="telegram_html",
                body=markdown_to_telegram_html(source),
                plain_text=plain_text,
                document=document,
            )
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document)

    if channel_id == "slack":
        if prefer_rich:
            return ChannelRenderResult(channel=channel_id, format="slack_mrkdwn", body=source, plain_text=plain_text, document=document)
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document)

    if channel_id == "discord":
        if prefer_rich:
            return ChannelRenderResult(channel=channel_id, format="discord_markdown", body=source, plain_text=plain_text, document=document)
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document)

    if channel_id in {"dingtalk", "wecom"}:
        if prefer_rich:
            return ChannelRenderResult(channel=channel_id, format="markdown", body=source, plain_text=plain_text, document=document)
        return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document)

    return ChannelRenderResult(channel=channel_id, format="plain", body=plain_text, plain_text=plain_text, document=document)


def build_channel_prompt_hint(channel: str) -> str:
    channel_id = (channel or "").strip().lower()
    caps = CHANNEL_CAPABILITIES.get(channel_id)
    format_hint = _CHANNEL_FORMAT_HINTS.get(channel_id)
    if not caps or not format_hint:
        return ""
    lines = [f"## Channel: {channel_id.replace('_', ' ').title()}", format_hint]
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
