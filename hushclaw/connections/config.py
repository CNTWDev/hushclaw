"""Config normalization between unified ``connections`` and legacy sections."""
from __future__ import annotations

from copy import deepcopy


APP_PROVIDERS = {"github", "google_workspace", "notion", "jira", "reddit", "x"}
CHANNEL_PROVIDERS = {"telegram", "feishu", "discord", "slack", "dingtalk", "wecom"}
SYNC_PROVIDERS = {"email", "calendar"}


def _clean_scalar(value):
    if isinstance(value, str):
        return value.strip()
    return value


def _clean_entry(raw: dict) -> dict:
    return {str(k): _clean_scalar(v) for k, v in raw.items()}


def _flat_connection_entry(*, kind: str, provider: str, **fields) -> dict:
    entry = {"kind": kind, "provider": provider}
    for key, value in fields.items():
        if value is None:
            continue
        entry[key] = value
    return entry


def connections_raw_to_legacy(connections: dict) -> dict:
    """Map flat ``connections`` tables into legacy config sections."""
    if not isinstance(connections, dict):
        return {}

    app_connectors: dict = {}
    channel_connectors: dict = {}
    emails: list[dict] = []
    calendars: list[dict] = []

    for _id, raw_entry in connections.items():
        if not isinstance(raw_entry, dict):
            continue
        entry = _clean_entry(raw_entry)
        kind = str(entry.get("kind") or "").strip()
        provider = str(entry.get("provider") or "").strip()
        if kind == "app" and provider in APP_PROVIDERS:
            app = {
                "enabled": bool(entry.get("enabled", False)),
                "allow_actions": bool(entry.get("allow_actions", False)),
            }
            for key in (
                "auth_mode", "auth_type", "default_repo",
                "client_id_ref", "client_secret_ref", "token_ref",
                "access_token_ref", "refresh_token_ref",
                "consumer_key_ref", "consumer_secret_ref",
                "oauth_client_id_ref", "oauth_client_secret_ref",
                "bearer_token_ref", "site_url", "email",
                "cloud_id", "workspace_name", "user_agent",
                "default_subreddit",
            ):
                if key in entry and entry[key] != "":
                    app[key] = entry[key]
            if "stream_enabled" in entry:
                app["stream_enabled"] = bool(entry.get("stream_enabled"))
            if isinstance(entry.get("scopes"), list):
                app["scopes"] = [str(v).strip() for v in entry["scopes"] if str(v).strip()]
            if isinstance(entry.get("stream_rules"), list):
                app["stream_rules"] = entry["stream_rules"]
            app_connectors[provider] = app
            continue

        if kind == "channel" and provider in CHANNEL_PROVIDERS:
            channel = {
                "enabled": bool(entry.get("enabled", False)),
            }
            for key in (
                "workspace", "agent", "bot_token", "app_id", "app_secret",
                "app_token", "client_id", "client_secret", "corp_id",
                "corp_secret", "verification_token", "verification_token_value",
                "signing_secret", "encrypt_key", "token",
            ):
                if key in entry and entry[key] != "":
                    channel[key] = entry[key]
            channel_connectors[provider] = channel
            continue

        if kind == "sync_source" and provider == "email":
            email = {}
            for key in (
                "label", "username", "password", "imap_host", "imap_port",
                "smtp_host", "smtp_port", "mailbox", "use_ssl", "use_tls",
                "enabled",
            ):
                if key in entry:
                    email[key] = entry[key]
            emails.append(email)
            continue

        if kind == "sync_source" and provider == "calendar":
            calendar = {}
            for key in (
                "label", "enabled", "url", "username", "password",
                "calendar_name", "sync_interval_minutes", "timezone",
            ):
                if key in entry:
                    calendar[key] = entry[key]
            calendars.append(calendar)

    out: dict = {}
    if app_connectors:
        out["app_connectors"] = app_connectors
    if channel_connectors:
        out["connectors"] = channel_connectors
    if emails:
        out["email"] = emails
    if calendars:
        out["calendar"] = calendars
    return out


def legacy_to_connections_raw(raw: dict, preferred: dict | None = None) -> dict[str, dict]:
    """Build flat ``connections`` tables from legacy config sections."""
    raw = deepcopy(raw or {})
    preferred = preferred if isinstance(preferred, dict) else {}
    out: dict[str, dict] = {}
    app_ids: dict[str, str] = {}
    channel_ids: dict[str, str] = {}
    email_ids: list[str] = []
    calendar_ids: list[str] = []

    for conn_id, section in preferred.items():
        if not isinstance(section, dict):
            continue
        kind = section.get("kind")
        provider = section.get("provider")
        if kind == "app" and provider in APP_PROVIDERS and provider not in app_ids:
            app_ids[provider] = str(conn_id)
        elif kind == "channel" and provider in CHANNEL_PROVIDERS and provider not in channel_ids:
            channel_ids[provider] = str(conn_id)
        elif kind == "sync_source" and provider == "email":
            email_ids.append(str(conn_id))
        elif kind == "sync_source" and provider == "calendar":
            calendar_ids.append(str(conn_id))

    app_raw = raw.get("app_connectors", {}) if isinstance(raw.get("app_connectors"), dict) else {}
    for provider in APP_PROVIDERS:
        section = app_raw.get(provider)
        if not isinstance(section, dict):
            continue
        fields = {
            "enabled": bool(section.get("enabled", False)),
            "allow_actions": bool(section.get("allow_actions", False)),
        }
        for key in (
            "auth_mode", "auth_type", "default_repo",
            "client_id_ref", "client_secret_ref", "token_ref",
            "access_token_ref", "refresh_token_ref",
            "consumer_key_ref", "consumer_secret_ref",
            "oauth_client_id_ref", "oauth_client_secret_ref",
            "bearer_token_ref", "site_url", "email",
            "cloud_id", "workspace_name", "user_agent",
            "default_subreddit",
        ):
            if key in section and section[key] != "":
                fields[key] = section[key]
        if "stream_enabled" in section:
            fields["stream_enabled"] = bool(section.get("stream_enabled"))
        if isinstance(section.get("scopes"), list):
            fields["scopes"] = section["scopes"]
        if isinstance(section.get("stream_rules"), list):
            fields["stream_rules"] = section["stream_rules"]
        out[app_ids.get(provider, provider)] = _flat_connection_entry(kind="app", provider=provider, **fields)

    conn_raw = raw.get("connectors", {}) if isinstance(raw.get("connectors"), dict) else {}
    for provider in CHANNEL_PROVIDERS:
        section = conn_raw.get(provider)
        if not isinstance(section, dict):
            continue
        fields = {"enabled": bool(section.get("enabled", False))}
        for key in (
            "workspace", "agent", "bot_token", "app_id", "app_secret",
            "app_token", "client_id", "client_secret", "corp_id",
            "corp_secret", "verification_token", "verification_token_value",
            "signing_secret", "encrypt_key", "token",
        ):
            if key in section and section[key] != "":
                fields[key] = section[key]
        out[channel_ids.get(provider, provider)] = _flat_connection_entry(kind="channel", provider=provider, **fields)

    emails = raw.get("email", [])
    if isinstance(emails, dict):
        emails = [emails]
    for idx, section in enumerate(emails):
        if not isinstance(section, dict):
            continue
        out[email_ids[idx] if idx < len(email_ids) else f"email_{idx + 1}"] = _flat_connection_entry(
            kind="sync_source",
            provider="email",
            label=section.get("label", ""),
            enabled=bool(section.get("enabled", False)),
            username=section.get("username", ""),
            password=section.get("password", ""),
            imap_host=section.get("imap_host", ""),
            imap_port=section.get("imap_port", 993),
            smtp_host=section.get("smtp_host", ""),
            smtp_port=section.get("smtp_port", 587),
            mailbox=section.get("mailbox", "INBOX"),
            use_ssl=bool(section.get("use_ssl", True)),
            use_tls=bool(section.get("use_tls", True)),
        )

    calendars = raw.get("calendar", [])
    if isinstance(calendars, dict):
        calendars = [calendars]
    for idx, section in enumerate(calendars):
        if not isinstance(section, dict):
            continue
        out[calendar_ids[idx] if idx < len(calendar_ids) else f"calendar_{idx + 1}"] = _flat_connection_entry(
            kind="sync_source",
            provider="calendar",
            label=section.get("label", ""),
            enabled=bool(section.get("enabled", False)),
            url=section.get("url", ""),
            username=section.get("username", ""),
            password=section.get("password", ""),
            calendar_name=section.get("calendar_name", ""),
            sync_interval_minutes=section.get("sync_interval_minutes", 30),
            timezone=section.get("timezone", ""),
        )

    return out
