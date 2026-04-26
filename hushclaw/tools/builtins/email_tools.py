"""IMAP/SMTP email tools — stdlib only, zero extra deps.

Multiple accounts are supported. Use the `account` parameter (0-based index) to select
a specific account when more than one is configured.

Configure accounts in hushclaw.toml using array-of-tables syntax:
    [[email]]
    label = "Personal"
    enabled = true
    imap_host = "imap.gmail.com"
    imap_port = 993
    smtp_host = "smtp.gmail.com"
    smtp_port = 587
    username = "you@gmail.com"
    password = "app-password-here"

    [[email]]
    label = "Work"
    enabled = true
    ...

Single-account config ([email] section) is still supported for backward compatibility.
"""
from __future__ import annotations

import email as _email_lib
import imaplib
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header as _decode_header

from hushclaw.tools.base import tool, ToolResult
from hushclaw.util.ssl_context import make_ssl_context


def _get_email_config(cfg, account: int):
    """Return the EmailConfig for the given account index, or raise ValueError."""
    accounts = getattr(cfg, "emails", [])
    if not accounts:
        raise ValueError("No email accounts configured. Add an [[email]] section to hushclaw.toml.")
    if account < 0 or account >= len(accounts):
        raise ValueError(f"Email account {account} does not exist (configured: {len(accounts)}).")
    acct = accounts[account]
    if not acct.enabled:
        label = f" ({acct.label})" if acct.label else f" ({acct.username})"
        raise ValueError(f"Email account {account}{label} is not enabled.")
    return acct


def _imap_conn(email_cfg):
    """Return a logged-in IMAP4_SSL connection from an EmailConfig."""
    ctx = make_ssl_context()
    conn = imaplib.IMAP4_SSL(email_cfg.imap_host, email_cfg.imap_port, ssl_context=ctx)
    conn.login(email_cfg.username, email_cfg.password)
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
    "Returns uid, from, to, subject, and date for each message. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def list_emails(
    folder: str = "INBOX",
    limit: int = 20,
    unread_only: bool = False,
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(folder, readonly=True)
            criterion = "UNSEEN" if unread_only else "ALL"
            status, data = conn.search(None, criterion)
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
        return ToolResult.error(f"list_emails failed: {e}")


@tool(description=(
    "Read the full body of an email by its UID. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def read_email(uid: str, folder: str = "INBOX", account: int = 0, _config=None) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
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
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = email_cfg.username
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "plain", "utf-8"))

        recipients = [addr.strip() for addr in to.split(",")]
        if cc:
            recipients += [addr.strip() for addr in cc.split(",")]

        with smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port, timeout=30) as server:
            if email_cfg.use_tls:
                server.starttls(context=make_ssl_context())
            server.login(email_cfg.username, email_cfg.password)
            server.send_message(msg, to_addrs=recipients)

        return ToolResult.ok(f"Email sent to {to}" + (f", cc {cc}" if cc else ""))
    except Exception as e:
        return ToolResult.error(f"send_email failed: {e}")


@tool(description=(
    "Search emails using IMAP SEARCH criteria. "
    "query examples: 'FROM user@example.com', 'SUBJECT meeting', 'SINCE 01-Jan-2026'. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def search_emails(
    query: str,
    folder: str = "INBOX",
    limit: int = 10,
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(folder, readonly=True)
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


@tool(description=(
    "Mark an email as read or unread by its UID. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def mark_email_read(
    uid: str,
    read: bool = True,
    folder: str = "INBOX",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
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


@tool(description=(
    "List all available IMAP folders/mailboxes. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def list_email_folders(account: int = 0, _config=None) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            status, data = conn.list()
            if status != "OK":
                return ToolResult.error(f"IMAP LIST failed: {status}")
            folders = []
            for item in data:
                if item:
                    decoded = item.decode() if isinstance(item, bytes) else str(item)
                    parts = decoded.rsplit('"', 2)
                    name = parts[-1].strip().strip('"') if len(parts) >= 2 else decoded
                    folders.append(name)
            return ToolResult.ok(json.dumps(folders, ensure_ascii=False, indent=2))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"list_email_folders failed: {e}")


@tool(description=(
    "Reply to an email by its UID. "
    "Sets In-Reply-To and References headers and quotes the original body. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def reply_email(
    uid: str,
    body: str,
    folder: str = "INBOX",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(folder, readonly=True)
            status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                return ToolResult.error(f"Could not fetch email uid={uid}")
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        orig = _email_lib.message_from_bytes(raw)
        orig_from = _decode_str(orig.get("From", ""))
        orig_subject = _decode_str(orig.get("Subject", ""))
        orig_message_id = orig.get("Message-ID", "")
        orig_references = orig.get("References", "")
        orig_body = _get_body(orig)

        reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
        msg = MIMEMultipart("alternative")
        msg["From"] = email_cfg.username
        msg["To"] = orig_from
        msg["Subject"] = reply_subject
        if orig_message_id:
            msg["In-Reply-To"] = orig_message_id
            refs = f"{orig_references} {orig_message_id}".strip() if orig_references else orig_message_id
            msg["References"] = refs

        quoted = "\n".join(f"> {line}" for line in orig_body.splitlines())
        msg.attach(MIMEText(f"{body}\n\n{quoted}", "plain", "utf-8"))

        recipients = [addr.strip() for addr in orig_from.split(",")]
        with smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port, timeout=30) as server:
            if email_cfg.use_tls:
                server.starttls(context=make_ssl_context())
            server.login(email_cfg.username, email_cfg.password)
            server.send_message(msg, to_addrs=recipients)
        return ToolResult.ok(f"Reply sent to {orig_from}")
    except Exception as e:
        return ToolResult.error(f"reply_email failed: {e}")


@tool(description=(
    "Delete an email by its UID. "
    "If trash_folder is given, copies to that folder first; then marks \\Deleted and expunges. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def delete_email(
    uid: str,
    folder: str = "INBOX",
    trash_folder: str = "",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(folder)
            if trash_folder:
                status, _ = conn.uid("COPY", uid.encode(), trash_folder)
                if status != "OK":
                    return ToolResult.error(f"COPY to {trash_folder} failed: {status}")
            conn.uid("STORE", uid.encode(), "+FLAGS", r"(\Deleted)")
            conn.expunge()
            action = f"moved to {trash_folder} and deleted from {folder}" if trash_folder else f"deleted from {folder}"
            return ToolResult.ok(f"Email uid={uid} {action}")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return ToolResult.error(f"delete_email failed: {e}")


@tool(description=(
    "Forward an email to another address by its UID. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def forward_email(
    uid: str,
    to: str,
    note: str = "",
    folder: str = "INBOX",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(folder, readonly=True)
            status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                return ToolResult.error(f"Could not fetch email uid={uid}")
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        orig = _email_lib.message_from_bytes(raw)
        orig_subject = _decode_str(orig.get("Subject", ""))
        orig_from = _decode_str(orig.get("From", ""))
        orig_body = _get_body(orig)

        fwd_subject = orig_subject if orig_subject.lower().startswith("fwd:") else f"Fwd: {orig_subject}"
        header = f"---------- Forwarded message ----------\nFrom: {orig_from}\nSubject: {orig_subject}\n\n"
        full_body = f"{note}\n\n{header}{orig_body}" if note else f"{header}{orig_body}"

        msg = MIMEMultipart("alternative")
        msg["From"] = email_cfg.username
        msg["To"] = to
        msg["Subject"] = fwd_subject
        msg.attach(MIMEText(full_body, "plain", "utf-8"))

        recipients = [addr.strip() for addr in to.split(",")]
        with smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port, timeout=30) as server:
            if email_cfg.use_tls:
                server.starttls(context=make_ssl_context())
            server.login(email_cfg.username, email_cfg.password)
            server.send_message(msg, to_addrs=recipients)
        return ToolResult.ok(f"Email forwarded to {to}")
    except Exception as e:
        return ToolResult.error(f"forward_email failed: {e}")


@tool(description=(
    "Move an email to a different folder by its UID. "
    "Use account=N (0-based) to select a specific email account when multiple are configured."
))
def move_email(
    uid: str,
    dest_folder: str,
    src_folder: str = "INBOX",
    account: int = 0,
    _config=None,
) -> ToolResult:
    if not (_config and getattr(_config, "emails", None)):
        return ToolResult.error("Email not configured. Add an [[email]] section to hushclaw.toml.")
    try:
        email_cfg = _get_email_config(_config, account)
    except ValueError as e:
        return ToolResult.error(str(e))
    try:
        conn = _imap_conn(email_cfg)
        try:
            conn.select(src_folder)
            result = conn.uid("MOVE", uid.encode(), dest_folder)
            if result[0] != "OK":
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
