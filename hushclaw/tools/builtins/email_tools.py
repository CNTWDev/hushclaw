"""IMAP/SMTP email tools — stdlib only, zero extra deps.

Enable these tools by adding them to tools.enabled in your config:
    tools.enabled = [..., "list_emails", "read_email", "send_email",
                         "search_emails", "mark_email_read", "move_email"]

Also configure the [email] section:
    [email]
    enabled = true
    imap_host = "imap.gmail.com"
    imap_port = 993
    smtp_host = "smtp.gmail.com"
    smtp_port = 587
    username = "you@gmail.com"
    password = "app-password-here"
"""
from __future__ import annotations

import email as _email_lib
import imaplib
import json
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header as _decode_header

from hushclaw.tools.base import tool, ToolResult


def _imap_conn(cfg):
    """Return a logged-in IMAP4_SSL connection."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(cfg.email.imap_host, cfg.email.imap_port, ssl_context=ctx)
    conn.login(cfg.email.username, cfg.email.password)
    return conn


def _decode_str(value: str | bytes) -> str:
    """Decode an RFC 2047 encoded header value to a plain string."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    parts = []
    for fragment, charset in _decode_header(value):
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(fragment)
    return "".join(parts)


def _parse_envelope(uid: bytes, raw_msg: bytes) -> dict:
    """Parse a raw RFC 822 message into a summary dict."""
    msg = _email_lib.message_from_bytes(raw_msg)
    return {
        "uid":     uid.decode() if isinstance(uid, bytes) else str(uid),
        "from":    _decode_str(msg.get("From", "")),
        "to":      _decode_str(msg.get("To", "")),
        "subject": _decode_str(msg.get("Subject", "(no subject)")),
        "date":    msg.get("Date", ""),
    }


def _get_body(msg) -> str:
    """Extract plain-text body from an email.message.Message object."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace")
        # Fallback: first text/html part
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    return f"[HTML]\n{payload.decode(charset, errors='replace')}"
        return "(no text content)"
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(charset, errors="replace")
        return "(empty)"


@tool(description=(
    "List recent emails from a mailbox folder. "
    "Returns uid, from, to, subject, and date for each message."
))
def list_emails(
    folder: str = "INBOX",
    limit: int = 20,
    unread_only: bool = False,
    _config=None,
) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    try:
        conn = _imap_conn(_config)
        try:
            conn.select(folder, readonly=True)
            criterion = "UNSEEN" if unread_only else "ALL"
            status, data = conn.search(None, criterion)
            if status != "OK":
                return ToolResult.error(f"IMAP search failed: {status}")
            uids = data[0].split()
            uids = uids[-limit:]  # most recent N
            results = []
            for uid in reversed(uids):
                status2, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                if status2 != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                results.append(_parse_envelope(uid, raw))
            return ToolResult.ok(json.dumps(results, ensure_ascii=False, indent=2))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"list_emails failed: {e}")


@tool(description="Read the full body of an email by its UID.")
def read_email(uid: str, folder: str = "INBOX", _config=None) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    try:
        conn = _imap_conn(_config)
        try:
            conn.select(folder, readonly=True)
            status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                return ToolResult.error(f"Could not fetch email uid={uid}")
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
            msg = _email_lib.message_from_bytes(raw)
            envelope = _parse_envelope(uid.encode(), raw)
            body = _get_body(msg)
            result = {**envelope, "body": body}
            return ToolResult.ok(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"read_email failed: {e}")


@tool(description=(
    "Send an email via SMTP. "
    "Uses the configured SMTP host/port and credentials."
))
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    _config=None,
) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    cfg = _config.email
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = cfg.username
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "plain", "utf-8"))

        recipients = [addr.strip() for addr in to.split(",")]
        if cc:
            recipients += [addr.strip() for addr in cc.split(",")]

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
            if cfg.use_tls:
                server.starttls(context=ssl.create_default_context())
            server.login(cfg.username, cfg.password)
            server.send_message(msg, to_addrs=recipients)

        return ToolResult.ok(f"Email sent to {to}" + (f", cc {cc}" if cc else ""))
    except Exception as e:
        return ToolResult.error(f"send_email failed: {e}")


@tool(description=(
    "Search emails using IMAP SEARCH criteria. "
    "query examples: 'FROM user@example.com', 'SUBJECT meeting', 'SINCE 01-Jan-2026'."
))
def search_emails(
    query: str,
    folder: str = "INBOX",
    limit: int = 10,
    _config=None,
) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    try:
        conn = _imap_conn(_config)
        try:
            conn.select(folder, readonly=True)
            # query may be multi-word IMAP search criteria
            status, data = conn.search(None, *query.split())
            if status != "OK":
                return ToolResult.error(f"IMAP search failed: {status}")
            uids = data[0].split()
            uids = uids[-limit:]
            results = []
            for uid in reversed(uids):
                status2, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                if status2 != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                results.append(_parse_envelope(uid, raw))
            return ToolResult.ok(json.dumps(results, ensure_ascii=False, indent=2))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"search_emails failed: {e}")


@tool(description="Mark an email as read or unread by its UID.")
def mark_email_read(uid: str, read: bool = True, folder: str = "INBOX", _config=None) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    try:
        conn = _imap_conn(_config)
        try:
            conn.select(folder)
            flag_cmd = "+FLAGS" if read else "-FLAGS"
            status, _ = conn.store(uid.encode(), flag_cmd, r"(\Seen)")
            if status != "OK":
                return ToolResult.error(f"IMAP store failed: {status}")
            action = "read" if read else "unread"
            return ToolResult.ok(f"Email uid={uid} marked as {action}")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"mark_email_read failed: {e}")


@tool(description="Move an email to a different folder by its UID.")
def move_email(uid: str, dest_folder: str, src_folder: str = "INBOX", _config=None) -> ToolResult:
    if not (_config and _config.email.enabled):
        return ToolResult.error("Email not configured. Set [email] enabled = true in config.")
    try:
        conn = _imap_conn(_config)
        try:
            conn.select(src_folder)
            # IMAP MOVE (RFC 6851) if supported, otherwise COPY + DELETE
            result = conn.uid("MOVE", uid.encode(), dest_folder)
            if result[0] != "OK":
                # Fallback: COPY + STORE \Deleted + EXPUNGE
                status, _ = conn.uid("COPY", uid.encode(), dest_folder)
                if status != "OK":
                    return ToolResult.error(f"COPY to {dest_folder} failed: {status}")
                conn.uid("STORE", uid.encode(), "+FLAGS", r"(\Deleted)")
                conn.expunge()
            return ToolResult.ok(f"Email uid={uid} moved to {dest_folder}")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"move_email failed: {e}")
