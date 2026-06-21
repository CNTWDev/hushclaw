"""Unified Connections status projection for WebUI and settings surfaces."""
from __future__ import annotations

from hushclaw.rich_content import CHANNEL_CAPABILITIES, get_channel_render_mode_label


def _channel_capability_labels(provider: str) -> tuple[list[str], dict]:
    caps = CHANNEL_CAPABILITIES.get(provider)
    if not caps:
        return (["Inbound Text", "Reply"], {})
    labels = ["Inbound Text", "Reply"]
    if caps.threaded_reply:
        labels.append("Threaded Reply")
    if caps.rich_text:
        labels.append("Rich Text")
    if caps.attachments:
        labels.append("Attachments")
    if caps.voice:
        labels.append("Voice")
    if caps.quote_context:
        labels.append("Quote Context")
    if caps.delivery_state:
        labels.append("Delivery State")
    if caps.approval_required_actions:
        labels.append("Approvals")
    if caps.background_wakeups:
        labels.append("Background Wakeups")
    return (
        labels,
        {
            "inbound_text": caps.inbound_text,
            "threaded_reply": caps.threaded_reply,
            "rich_text": caps.rich_text,
            "attachments": caps.attachments,
            "voice": caps.voice,
            "quote_context": caps.quote_context,
            "delivery_state": caps.delivery_state,
            "approval_required_actions": caps.approval_required_actions,
            "background_wakeups": caps.background_wakeups,
            "max_message_len": caps.max_message_len,
        },
    )


def _item(
    *,
    id: str,
    kind: str,
    provider: str,
    name: str,
    description: str,
    capabilities: list[str],
    enabled: bool,
    configured: bool,
    connected: bool = False,
    auth: str = "",
    manage_target: str = "settings",
    manage_id: str = "",
    meta: dict | None = None,
) -> dict:
    state = "connected" if connected else "configured" if configured else "disabled"
    if enabled and configured and not connected:
        state = "enabled"
    if enabled and not configured:
        state = "needs_config"
    return {
        "id": id,
        "kind": kind,
        "provider": provider,
        "name": name,
        "description": description,
        "capabilities": capabilities,
        "enabled": bool(enabled),
        "configured": bool(configured),
        "connected": bool(connected),
        "state": state,
        "auth": auth,
        "manage_target": manage_target,
        "manage_id": manage_id or id,
        "meta": meta or {},
    }


def _app_connector_items(cfg, secret_store) -> list[dict]:
    app = cfg.app_connectors
    return [
        _item(
            id="github",
            kind="app",
            provider="github",
            name="GitHub",
            description="Repository search, read, issues, pull requests, and code context.",
            capabilities=["Search", "Read", "Sources"],
            enabled=app.github.enabled,
            configured=secret_store.is_set(app.github.token_ref),
            auth="GitHub OAuth or fine-grained token",
            manage_target="panel",
            meta={"allow_actions": bool(app.github.allow_actions), "default_repo": app.github.default_repo or ""},
        ),
        _item(
            id="google_workspace",
            kind="app",
            provider="google_workspace",
            name="Google Workspace",
            description="Drive, Gmail, and Calendar app tools through Google APIs.",
            capabilities=["Drive", "Gmail", "Calendar"],
            enabled=app.google_workspace.enabled,
            configured=secret_store.is_set(app.google_workspace.refresh_token_ref) or secret_store.is_set(app.google_workspace.access_token_ref),
            auth="OAuth 2.0",
            manage_target="panel",
            meta={"allow_actions": bool(app.google_workspace.allow_actions)},
        ),
        _item(
            id="notion",
            kind="app",
            provider="notion",
            name="Notion",
            description="Workspace search and reading via a built-in Notion adapter.",
            capabilities=["Search", "Read", "Docs"],
            enabled=app.notion.enabled,
            configured=secret_store.is_set(app.notion.token_ref),
            auth="Internal token or OAuth",
            manage_target="panel",
            meta={"allow_actions": bool(app.notion.allow_actions), "workspace_name": app.notion.workspace_name or ""},
        ),
        _item(
            id="jira",
            kind="app",
            provider="jira",
            name="Jira",
            description="Issue, project, and work-item context through Jira Cloud.",
            capabilities=["Issues", "Projects", "Search"],
            enabled=app.jira.enabled,
            configured=(
                secret_store.is_set(app.jira.token_ref)
                or secret_store.is_set(app.jira.access_token_ref)
            ),
            auth="OAuth or API token",
            manage_target="panel",
            meta={"allow_actions": bool(app.jira.allow_actions), "site_url": app.jira.site_url or ""},
        ),
        _item(
            id="reddit",
            kind="app",
            provider="reddit",
            name="Reddit",
            description="Subreddit search, reading, posting, and commenting.",
            capabilities=["Search", "Read", "Post", "Comment"],
            enabled=app.reddit.enabled,
            configured=secret_store.is_set(app.reddit.access_token_ref),
            auth="OAuth",
            manage_target="panel",
            meta={"allow_actions": bool(app.reddit.allow_actions), "default_subreddit": app.reddit.default_subreddit or ""},
        ),
        _item(
            id="x",
            kind="app",
            provider="x",
            name="X",
            description="Search, read, post, reply, and optional outbound stream rules.",
            capabilities=["Search", "Read", "Post", "Reply", "Stream"],
            enabled=app.x.enabled,
            configured=secret_store.is_set(app.x.bearer_token_ref) or secret_store.is_set(app.x.access_token_ref),
            auth="OAuth or app keys",
            manage_target="panel",
            meta={"allow_actions": bool(app.x.allow_actions), "stream_enabled": bool(app.x.stream_enabled)},
        ),
    ]


def _channel_items(cfg, connector_status: dict[str, bool]) -> list[dict]:
    c = cfg.connectors
    telegram_caps, telegram_matrix = _channel_capability_labels("telegram")
    feishu_caps, feishu_matrix = _channel_capability_labels("feishu")
    discord_caps, discord_matrix = _channel_capability_labels("discord")
    slack_caps, slack_matrix = _channel_capability_labels("slack")
    dingtalk_caps, dingtalk_matrix = _channel_capability_labels("dingtalk")
    wecom_caps, wecom_matrix = _channel_capability_labels("wecom")
    whatsapp_caps, whatsapp_matrix = _channel_capability_labels("whatsapp")
    return [
        _item(
            id="telegram",
            kind="channel",
            provider="telegram",
            name="Telegram",
            description="Inbound and outbound Telegram bot channel for agent chat.",
            capabilities=telegram_caps,
            enabled=c.telegram.enabled,
            configured=bool(c.telegram.bot_token),
            connected=bool(connector_status.get("telegram")),
            auth="Bot token",
            meta={
                "agent": c.telegram.agent,
                "workspace": c.telegram.workspace,
                "render_mode": c.telegram.render_mode,
                "render_mode_label": get_channel_render_mode_label("telegram", c.telegram.render_mode),
                "channel_capabilities": telegram_matrix,
            },
        ),
        _item(
            id="feishu",
            kind="channel",
            provider="feishu",
            name="Feishu",
            description="Inbound and outbound Feishu/Lark channel connector.",
            capabilities=feishu_caps,
            enabled=c.feishu.enabled,
            configured=bool(c.feishu.app_id and c.feishu.app_secret),
            connected=bool(connector_status.get("feishu")),
            auth="App ID + App Secret",
            meta={
                "agent": c.feishu.agent,
                "workspace": c.feishu.workspace,
                "render_mode": c.feishu.render_mode,
                "render_mode_label": get_channel_render_mode_label("feishu", c.feishu.render_mode),
                "channel_capabilities": feishu_matrix,
            },
        ),
        _item(
            id="discord",
            kind="channel",
            provider="discord",
            name="Discord",
            description="Inbound and outbound Discord bot connector.",
            capabilities=discord_caps,
            enabled=c.discord.enabled,
            configured=bool(c.discord.bot_token),
            connected=bool(connector_status.get("discord")),
            auth="Bot token",
            meta={
                "agent": c.discord.agent,
                "workspace": c.discord.workspace,
                "render_mode": c.discord.render_mode,
                "render_mode_label": get_channel_render_mode_label("discord", c.discord.render_mode),
                "channel_capabilities": discord_matrix,
            },
        ),
        _item(
            id="slack",
            kind="channel",
            provider="slack",
            name="Slack",
            description="Slack Socket Mode connector for inbound and outbound agent chat.",
            capabilities=slack_caps,
            enabled=c.slack.enabled,
            configured=bool(c.slack.bot_token and c.slack.app_token),
            connected=bool(connector_status.get("slack")),
            auth="Bot token + App token",
            meta={
                "agent": c.slack.agent,
                "workspace": c.slack.workspace,
                "render_mode": c.slack.render_mode,
                "render_mode_label": get_channel_render_mode_label("slack", c.slack.render_mode),
                "channel_capabilities": slack_matrix,
            },
        ),
        _item(
            id="dingtalk",
            kind="channel",
            provider="dingtalk",
            name="DingTalk",
            description="Inbound and outbound DingTalk connector.",
            capabilities=dingtalk_caps,
            enabled=c.dingtalk.enabled,
            configured=bool(c.dingtalk.client_id and c.dingtalk.client_secret),
            connected=bool(connector_status.get("dingtalk")),
            auth="Client ID + Client Secret",
            meta={
                "agent": c.dingtalk.agent,
                "workspace": c.dingtalk.workspace,
                "render_mode": c.dingtalk.render_mode,
                "render_mode_label": get_channel_render_mode_label("dingtalk", c.dingtalk.render_mode),
                "channel_capabilities": dingtalk_matrix,
            },
        ),
        _item(
            id="wecom",
            kind="channel",
            provider="wecom",
            name="WeCom",
            description="Inbound and outbound WeChat Work connector.",
            capabilities=wecom_caps,
            enabled=c.wecom.enabled,
            configured=bool(c.wecom.corp_id and c.wecom.corp_secret),
            connected=bool(connector_status.get("wecom")),
            auth="Corp ID + Corp Secret",
            meta={
                "agent": c.wecom.agent,
                "workspace": c.wecom.workspace,
                "render_mode": c.wecom.render_mode,
                "render_mode_label": get_channel_render_mode_label("wecom", c.wecom.render_mode),
                "channel_capabilities": wecom_matrix,
            },
        ),
        _item(
            id="whatsapp",
            kind="channel",
            provider="whatsapp",
            name="WhatsApp",
            description="Inbound and outbound WhatsApp connector via Twilio webhook and REST delivery.",
            capabilities=whatsapp_caps,
            enabled=c.whatsapp.enabled,
            configured=bool(c.whatsapp.account_sid and c.whatsapp.auth_token and c.whatsapp.from_number),
            connected=bool(connector_status.get("whatsapp")),
            auth="Twilio Account SID + Auth Token",
            meta={
                "agent": c.whatsapp.agent,
                "workspace": c.whatsapp.workspace,
                "render_mode": c.whatsapp.render_mode,
                "render_mode_label": get_channel_render_mode_label("whatsapp", c.whatsapp.render_mode),
                "channel_capabilities": whatsapp_matrix,
            },
        ),
    ]


def _sync_source_items(cfg) -> list[dict]:
    items: list[dict] = []
    for idx, acct in enumerate(getattr(cfg, "emails", []) or []):
        items.append(_item(
            id=f"email:{idx}",
            kind="sync_source",
            provider="email",
            name=acct.label or f"Email {idx + 1}",
            description="IMAP/SMTP mailbox account used by built-in email tools.",
            capabilities=["Inbox", "Read", "Send"],
            enabled=acct.enabled,
            configured=bool(acct.imap_host and acct.smtp_host and acct.username and acct.password),
            auth="Username + app password",
            meta={"index": idx, "username": acct.username or "", "mailbox": acct.mailbox or "INBOX"},
        ))
    for idx, cal in enumerate(getattr(cfg, "calendars", []) or []):
        items.append(_item(
            id=f"calendar:{idx}",
            kind="sync_source",
            provider="calendar",
            name=cal.label or f"Calendar {idx + 1}",
            description="External calendar sync source feeding the local calendar store.",
            capabilities=["Sync", "Events"],
            enabled=cal.enabled,
            configured=bool(cal.url and cal.username and cal.password),
            auth="CalDAV URL + app password",
            meta={"index": idx, "url": cal.url or "", "calendar_name": cal.calendar_name or "", "timezone": cal.timezone or ""},
        ))
    return items


def build_connections_view(cfg, connector_status: dict[str, bool] | None = None, secret_store=None) -> list[dict]:
    connector_status = connector_status or {}
    if secret_store is None:
        from hushclaw.secrets.store import get_secret_store
        secret_store = get_secret_store()
    items: list[dict] = []
    items.extend(_app_connector_items(cfg, secret_store))
    items.extend(_channel_items(cfg, connector_status))
    items.extend(_sync_source_items(cfg))
    return items
