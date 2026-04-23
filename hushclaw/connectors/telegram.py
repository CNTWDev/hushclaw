"""Telegram Bot connector — uses urllib long-polling (zero new dependencies)."""
from __future__ import annotations

import asyncio
import json
import re
import urllib.request

from hushclaw.connectors.base import Connector, log
from hushclaw.util.ssl_context import make_ssl_context

# ---------------------------------------------------------------------------
# Markdown → Telegram HTML helpers
# ---------------------------------------------------------------------------

_RE_FENCE   = re.compile(r'```(\w*)\n?([\s\S]*?)```')
_RE_INLCODE = re.compile(r'`([^`\n]+)`')
_RE_HEADER  = re.compile(r'^#{1,6}\s+(.+)$', re.MULTILINE)
_RE_BOLD    = re.compile(r'\*\*(.+?)\*\*|__(.+?)__', re.DOTALL)
_RE_ITALIC  = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!\w)_([^_\n]+?)_(?!\w)', re.DOTALL)
_RE_STRIKE  = re.compile(r'~~(.+?)~~', re.DOTALL)
_RE_LINK    = re.compile(r'\[([^\]\n]+)\]\(([^)\n]+)\)')


def _html_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_to_tg_html(text: str) -> str:
    """Convert typical LLM Markdown output to Telegram HTML (parse_mode=HTML).

    Strategy: extract code spans first (to protect their content), then
    HTML-escape the remaining text, apply other formatting conversions,
    and finally restore code spans.
    """
    placeholders: dict[str, str] = {}
    counter = [0]

    def _store(html: str) -> str:
        key = f"\x00{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = html
        return key

    def _fence_sub(m: re.Match) -> str:
        lang = m.group(1)
        code = _html_esc(m.group(2).strip())
        tag  = f'<pre><code class="language-{lang}">{code}</code></pre>' if lang else f"<pre>{code}</pre>"
        return _store(tag)

    def _inline_sub(m: re.Match) -> str:
        return _store(f"<code>{_html_esc(m.group(1))}</code>")

    text = _RE_FENCE.sub(_fence_sub, text)
    text = _RE_INLCODE.sub(_inline_sub, text)

    text = _html_esc(text)

    text = _RE_HEADER.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _RE_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _RE_ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _RE_STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _RE_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)

    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


# ---------------------------------------------------------------------------
# Plain-text helpers
# ---------------------------------------------------------------------------

_RE_STRIP_FENCE   = re.compile(r'```\w*\n?([\s\S]*?)```')
_RE_STRIP_INLCODE = re.compile(r'`([^`\n]+)`')
_RE_STRIP_HEADER  = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_RE_STRIP_BOLD    = re.compile(r'\*\*(.+?)\*\*|__(.+?)__', re.DOTALL)
_RE_STRIP_ITALIC  = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!\w)_([^_\n]+?)_(?!\w)')
_RE_STRIP_STRIKE  = re.compile(r'~~(.+?)~~', re.DOTALL)
_RE_STRIP_LINK    = re.compile(r'\[([^\]\n]+)\]\([^)\n]+\)')


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax markers, keeping content as clean plain text.

    Used when HTML conversion fails so the user sees readable text
    instead of raw ``**bold**`` or ``# header`` characters.
    """
    text = _RE_STRIP_FENCE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_INLCODE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_HEADER.sub('', text)
    text = _RE_STRIP_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _RE_STRIP_ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _RE_STRIP_STRIKE.sub(lambda m: m.group(1), text)
    text = _RE_STRIP_LINK.sub(lambda m: m.group(1), text)
    return text


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split text into chunks of at most *max_len* characters.

    Splits at paragraph boundaries (double newlines) to avoid cutting in the
    middle of a sentence. Adds ``(N/M)`` indicators when multiple parts are
    produced.
    """
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        chunk = remaining[:max_len]
        split_at = chunk.rfind('\n\n')
        if split_at < max_len // 2:
            split_at = chunk.rfind('\n')
        if split_at < 0:
            split_at = max_len
        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip('\n')
    if remaining:
        parts.append(remaining)

    total = len(parts)
    if total > 1:
        parts = [f"{p}\n({i + 1}/{total})" for i, p in enumerate(parts)]
    return parts


class TelegramConnector(Connector):
    _connector_name = "telegram"
    """Long-polls the Telegram Bot API and replies via sendMessage / editMessageText."""

    BASE = "https://api.telegram.org"

    def __init__(self, gateway, config) -> None:
        super().__init__(gateway, config)
        self._token: str = config.bot_token
        self._allowlist: list[int] = list(config.allowlist)
        self._group_allowlist: list[int] = list(config.group_allowlist)
        self._polling_timeout: int = config.polling_timeout
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        # A registered webhook blocks getUpdates with 409 Conflict.
        # Always delete it before switching to long-polling.
        try:
            await asyncio.to_thread(self._api, "deleteWebhook", drop_pending_updates=False)
            log.info("[telegram] webhook cleared (if any); starting long-poll")
        except Exception as exc:
            log.warning("[telegram] deleteWebhook failed (continuing anyway): %s", exc)
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-poll")
        log.info("[telegram] connector started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        log.info("[telegram] connector stopped")

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        offset = 0
        while self._running:
            try:
                updates = await asyncio.to_thread(self._get_updates, offset)
                for upd in updates:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if not msg:
                        continue
                    # Must have at least text or a file attachment
                    has_text = "text" in msg
                    has_file = any(k in msg for k in ("document", "photo", "audio", "video", "voice"))
                    if not has_text and not has_file:
                        continue
                    sender = msg.get("from", {})
                    user_id: int = sender.get("id", 0)
                    chat = msg.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    chat_type = chat.get("type", "private")

                    # Allowlist filtering
                    if self._allowlist and user_id not in self._allowlist:
                        continue
                    if chat_type in ("group", "supergroup"):
                        if self._group_allowlist and int(chat_id) not in self._group_allowlist:
                            continue

                    asyncio.create_task(
                        self._handle_telegram_message(chat_id, msg),
                        name=f"telegram-msg-{chat_id}",
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("[telegram] poll error: %s", exc)
                await asyncio.sleep(5)

    def _get_updates(self, offset: int) -> list[dict]:
        params = (
            f"offset={offset}"
            f"&timeout={self._polling_timeout}"
            "&allowed_updates=%5B%22message%22%5D"  # ["message"] — includes text + files
        )
        url = f"{self.BASE}/bot{self._token}/getUpdates?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "HushClaw/1.0"})
        with urllib.request.urlopen(
            req, timeout=self._polling_timeout + 5, context=make_ssl_context()
        ) as resp:
            return json.loads(resp.read()).get("result", [])

    # ------------------------------------------------------------------
    # Message handling (text + file attachments)
    # ------------------------------------------------------------------

    async def _handle_telegram_message(self, chat_id: str, msg: dict) -> None:
        text = msg.get("text") or msg.get("caption") or ""
        attachment_lines: list[str] = []

        # Determine file_id and filename for each attachment type
        file_entries: list[tuple[str, str]] = []  # (file_id, filename)
        if "document" in msg:
            doc = msg["document"]
            file_entries.append((doc["file_id"], doc.get("file_name") or "document"))
        if "photo" in msg:
            # photos is a list sorted by size; take the largest
            photo = msg["photo"][-1]
            file_entries.append((photo["file_id"], f"photo_{photo['file_id'][:8]}.jpg"))
        if "audio" in msg:
            audio = msg["audio"]
            file_entries.append((audio["file_id"], audio.get("file_name") or "audio.mp3"))
        if "video" in msg:
            video = msg["video"]
            file_entries.append((video["file_id"], video.get("file_name") or "video.mp4"))
        if "voice" in msg:
            voice = msg["voice"]
            file_entries.append((voice["file_id"], "voice.ogg"))

        for tg_file_id, filename in file_entries:
            local_path = await asyncio.to_thread(
                self._download_tg_file, tg_file_id, filename
            )
            if local_path:
                attachment_lines.append(f"- {filename} (local path: {local_path})")

        if attachment_lines:
            text = (text + "\n\n" if text else "") + "[Attached files]\n" + "\n".join(attachment_lines)

        if text.strip():
            await self._handle_message(chat_id, text)

    def _download_tg_file(self, file_id: str, filename: str) -> str | None:
        """Get file path from Telegram and download to upload_dir."""
        try:
            result = self._api("getFile", file_id=file_id)
            file_path = result["result"]["file_path"]
            url = f"{self.BASE}/file/bot{self._token}/{file_path}"
            return self._download_to_upload_dir(url, filename)
        except Exception as exc:
            log.warning("[telegram] failed to get file %s: %s", file_id, exc)
            return None

    # ------------------------------------------------------------------
    # Reply delivery
    # ------------------------------------------------------------------

    async def _send_reply(self, chat_id: str, text: str) -> None:
        """Send the complete reply, splitting into multiple messages if needed."""
        for part in _split_message(text):
            await asyncio.to_thread(self._send_message, chat_id, part)

    # ------------------------------------------------------------------
    # Low-level API wrappers (synchronous, run in thread)
    # ------------------------------------------------------------------

    def _api(self, method: str, **kwargs) -> dict:
        url = f"{self.BASE}/bot{self._token}/{method}"
        data = json.dumps(kwargs).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=make_ssl_context()) as resp:
            return json.loads(resp.read())

    def _send_message(self, chat_id: str, text: str) -> int:
        if self._markdown:
            try:
                result = self._api(
                    "sendMessage",
                    chat_id=chat_id,
                    text=_md_to_tg_html(text)[:4096],
                    parse_mode="HTML",
                )
                return result["result"]["message_id"]
            except Exception:
                pass  # fall back to clean plain text on HTML parse errors
        result = self._api("sendMessage", chat_id=chat_id, text=_strip_markdown(text)[:4096])
        return result["result"]["message_id"]
