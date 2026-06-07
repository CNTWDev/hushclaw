"""server/config_mixin.py — config status, apply, playwright check, list models,
and thin delegators to provider/skill/transsion/update/config handlers.

Extracted from server_impl.py. All methods are accessed via self (mixin pattern).
"""
from __future__ import annotations

import asyncio
import json

from hushclaw.server import provider_handler, skill_handler, transsion_handler, config_handler, update_handler, integration_handler
from hushclaw._build_info import BUILD_TIME as _BUILD_TIME
from hushclaw.util.logging import get_logger

log = get_logger("server")


class ConfigMixin:
    """Mixin for HushClawServer: config status, apply, playwright, models, and handler delegators."""

    # ── Playwright availability ────────────────────────────────────────────────

    def _check_playwright(self) -> bool:
        if self._playwright_available is None:
            try:
                import playwright.async_api  # noqa: F401
                self._playwright_available = True
            except ImportError:
                self._playwright_available = False
        return self._playwright_available

    # ── Config status ──────────────────────────────────────────────────────────

    def _config_status(self) -> dict:
        """Return current configuration state for the setup wizard."""
        cfg = self._gateway.base_agent.config
        provider = cfg.provider.name
        api_key = cfg.provider.api_key
        needs_key = "ollama" not in provider

        from hushclaw.config.loader import get_config_dir, _load_toml
        cfg_file_path = get_config_dir() / "hushclaw.toml"
        raw_cfg = _load_toml(cfg_file_path)
        raw_provider = raw_cfg.get("provider", {}) if isinstance(raw_cfg, dict) else {}
        if not isinstance(raw_provider, dict):
            raw_provider = {}
        api_key_saved = bool(str(raw_provider.get("api_key") or "").strip())
        raw_agent = raw_cfg.get("agent", {}) if isinstance(raw_cfg, dict) else {}
        if not isinstance(raw_agent, dict):
            raw_agent = {}
        from hushclaw.config.system_prompt import should_reset_persisted_system_prompt
        raw_system_prompt = str(raw_agent.get("system_prompt") or "").strip()
        system_prompt_custom = bool(raw_system_prompt) and not should_reset_persisted_system_prompt(raw_system_prompt)

        api_key_masked = ""
        if api_key_saved and api_key:
            api_key_masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) > 8 else "set"

        cfg_file = str(cfg_file_path)

        c   = cfg.connectors
        tg  = c.telegram
        fs  = c.feishu
        dc  = c.discord
        sl  = c.slack
        dt  = c.dingtalk
        wc  = c.wecom
        app = cfg.app_connectors
        gh  = app.github
        gw  = app.google_workspace
        nt  = app.notion
        jr  = app.jira
        rd  = app.reddit
        xc  = app.x
        upd = cfg.update
        last_update = self._update_service.last_result or {}
        from hushclaw.secrets import get_secret_store
        secrets = get_secret_store()
        return {
            "type": "config_status",
            "version":    self._update_service.current_version,
            "build_time": _BUILD_TIME,
            "configured": (not needs_key) or api_key_saved,
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "provider_timeout": cfg.provider.timeout,
            "public_base_url": cfg.server.public_base_url or "",
            "api_key_set": bool(api_key),
            "api_key_saved": api_key_saved,
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "cheap_model": cfg.agent.cheap_model or "",
            "max_tool_rounds": cfg.agent.max_tool_rounds,
            "system_prompt": cfg.agent.system_prompt,
            "system_prompt_custom": system_prompt_custom,
            "cost_per_1k_input_tokens": cfg.provider.cost_per_1k_input_tokens,
            "cost_per_1k_output_tokens": cfg.provider.cost_per_1k_output_tokens,
            "config_file": cfg_file,
            "update": {
                "auto_check_enabled": upd.auto_check_enabled,
                "check_interval_hours": upd.check_interval_hours,
                "channel": upd.channel,
                "last_checked_at": upd.last_checked_at or self._update_service.last_checked_at,
                "check_timeout_seconds": upd.check_timeout_seconds,
                "cache_ttl_seconds": upd.cache_ttl_seconds,
                "upgrade_timeout_seconds": upd.upgrade_timeout_seconds,
                "current_version": self._update_service.current_version,
                "latest_version": last_update.get("latest_version", ""),
                "update_available": bool(last_update.get("update_available", False)),
                "release_url": last_update.get("release_url", ""),
            },
            "connectors": {
                "telegram": {
                    "enabled":         tg.enabled,
                    "bot_token_set":   bool(tg.bot_token),
                    "agent":           tg.agent,
                    "workspace":       tg.workspace,
                    "allowlist":       tg.allowlist,
                    "group_allowlist": tg.group_allowlist,
                    "group_policy":    tg.group_policy,
                    "require_mention": tg.require_mention,
                    "markdown":        tg.markdown,
                },
                "feishu": {
                    "enabled":                fs.enabled,
                    "app_id":                 fs.app_id,
                    "app_secret_set":         bool(fs.app_secret),
                    "encrypt_key_set":        bool(fs.encrypt_key),
                    "verification_token_set": bool(fs.verification_token),
                    "agent":                  fs.agent,
                    "workspace":              fs.workspace,
                    "allowlist":              fs.allowlist,
                    "markdown":               fs.markdown,
                },
                "discord": {
                    "enabled":         dc.enabled,
                    "bot_token_set":   bool(dc.bot_token),
                    "agent":           dc.agent,
                    "workspace":       dc.workspace,
                    "allowlist":       dc.allowlist,
                    "guild_allowlist": dc.guild_allowlist,
                    "require_mention": dc.require_mention,
                    "stream":          dc.stream,
                    "markdown":        dc.markdown,
                },
                "slack": {
                    "enabled":       sl.enabled,
                    "bot_token_set": bool(sl.bot_token),
                    "app_token_set": bool(sl.app_token),
                    "agent":         sl.agent,
                    "workspace":     sl.workspace,
                    "allowlist":     sl.allowlist,
                    "stream":        sl.stream,
                    "markdown":      sl.markdown,
                },
                "dingtalk": {
                    "enabled":           dt.enabled,
                    "client_id":         dt.client_id,
                    "client_secret_set": bool(dt.client_secret),
                    "agent":             dt.agent,
                    "workspace":         dt.workspace,
                    "allowlist":         dt.allowlist,
                    "stream":            dt.stream,
                    "markdown":          dt.markdown,
                },
                "wecom": {
                    "enabled":          wc.enabled,
                    "corp_id":          wc.corp_id,
                    "corp_secret_set":  bool(wc.corp_secret),
                    "agent_id":         wc.agent_id,
                    "token_set":        bool(wc.token),
                    "agent":            wc.agent,
                    "workspace":        wc.workspace,
                    "allowlist":        wc.allowlist,
                    "markdown":         wc.markdown,
                },
            },
            "connector_status": self._connectors.status(),
            "app_connectors": {
                "broker_base_url": app.broker_base_url,
                "github": {
                    "enabled": gh.enabled,
                    "auth_mode": gh.auth_mode,
                    "auth_type": gh.auth_type,
                    "client_id_ref": gh.client_id_ref,
                    "client_id_set": secrets.is_set(gh.client_id_ref),
                    "client_secret_ref": gh.client_secret_ref,
                    "client_secret_set": secrets.is_set(gh.client_secret_ref),
                    "token_ref": gh.token_ref,
                    "token_set": secrets.is_set(gh.token_ref),
                    "default_repo": gh.default_repo,
                    "allow_actions": gh.allow_actions,
                },
                "google_workspace": {
                    "enabled": gw.enabled,
                    "auth_mode": gw.auth_mode,
                    "auth_type": gw.auth_type,
                    "client_id_ref": gw.client_id_ref,
                    "client_id_set": secrets.is_set(gw.client_id_ref),
                    "client_secret_ref": gw.client_secret_ref,
                    "client_secret_set": secrets.is_set(gw.client_secret_ref),
                    "access_token_ref": gw.access_token_ref,
                    "access_token_set": secrets.is_set(gw.access_token_ref),
                    "refresh_token_ref": gw.refresh_token_ref,
                    "refresh_token_set": secrets.is_set(gw.refresh_token_ref),
                    "scopes": gw.scopes,
                    "allow_actions": gw.allow_actions,
                },
                "notion": {
                    "enabled": nt.enabled,
                    "auth_mode": nt.auth_mode,
                    "auth_type": nt.auth_type,
                    "client_id_ref": nt.client_id_ref,
                    "client_id_set": secrets.is_set(nt.client_id_ref),
                    "client_secret_ref": nt.client_secret_ref,
                    "client_secret_set": secrets.is_set(nt.client_secret_ref),
                    "token_ref": nt.token_ref,
                    "token_set": secrets.is_set(nt.token_ref),
                    "workspace_name": nt.workspace_name,
                    "allow_actions": nt.allow_actions,
                },
                "jira": {
                    "enabled": jr.enabled,
                    "auth_mode": jr.auth_mode,
                    "auth_type": jr.auth_type,
                    "site_url": jr.site_url,
                    "email": jr.email,
                    "client_id_ref": jr.client_id_ref,
                    "client_id_set": secrets.is_set(jr.client_id_ref),
                    "client_secret_ref": jr.client_secret_ref,
                    "client_secret_set": secrets.is_set(jr.client_secret_ref),
                    "token_ref": jr.token_ref,
                    "token_set": secrets.is_set(jr.token_ref),
                    "access_token_ref": jr.access_token_ref,
                    "access_token_set": secrets.is_set(jr.access_token_ref),
                    "refresh_token_ref": jr.refresh_token_ref,
                    "refresh_token_set": secrets.is_set(jr.refresh_token_ref),
                    "cloud_id": jr.cloud_id,
                    "scopes": jr.scopes,
                    "allow_actions": jr.allow_actions,
                },
                "reddit": {
                    "enabled": rd.enabled,
                    "auth_mode": rd.auth_mode,
                    "auth_type": rd.auth_type,
                    "client_id_ref": rd.client_id_ref,
                    "client_id_set": secrets.is_set(rd.client_id_ref),
                    "client_secret_ref": rd.client_secret_ref,
                    "client_secret_set": secrets.is_set(rd.client_secret_ref),
                    "access_token_ref": rd.access_token_ref,
                    "access_token_set": secrets.is_set(rd.access_token_ref),
                    "refresh_token_ref": rd.refresh_token_ref,
                    "refresh_token_set": secrets.is_set(rd.refresh_token_ref),
                    "user_agent": rd.user_agent,
                    "default_subreddit": rd.default_subreddit,
                    "allow_actions": rd.allow_actions,
                },
                "x": {
                    "enabled": xc.enabled,
                    "auth_mode": xc.auth_mode,
                    "auth_type": xc.auth_type,
                    "consumer_key_ref": xc.consumer_key_ref,
                    "consumer_key_set": secrets.is_set(xc.consumer_key_ref),
                    "consumer_secret_ref": xc.consumer_secret_ref,
                    "consumer_secret_set": secrets.is_set(xc.consumer_secret_ref),
                    "oauth_client_id_ref": xc.oauth_client_id_ref,
                    "oauth_client_id_set": secrets.is_set(xc.oauth_client_id_ref),
                    "oauth_client_secret_ref": xc.oauth_client_secret_ref,
                    "oauth_client_secret_set": secrets.is_set(xc.oauth_client_secret_ref),
                    "bearer_token_ref": xc.bearer_token_ref,
                    "bearer_token_set": secrets.is_set(xc.bearer_token_ref),
                    "access_token_ref": xc.access_token_ref,
                    "access_token_set": secrets.is_set(xc.access_token_ref),
                    "refresh_token_ref": xc.refresh_token_ref,
                    "refresh_token_set": secrets.is_set(xc.refresh_token_ref),
                    "stream_enabled": xc.stream_enabled,
                    "stream_rules": xc.stream_rules,
                    "require_publish_confirmation": xc.require_publish_confirmation,
                    "allow_actions": xc.allow_actions,
                },
            },
            "browser": {
                "enabled":                cfg.browser.enabled,
                "headless":               cfg.browser.headless,
                "timeout":                cfg.browser.timeout,
                "playwright_installed":   self._check_playwright(),
                "use_user_chrome":        bool(cfg.browser.remote_debugging_url),
                "remote_debugging_url":   cfg.browser.remote_debugging_url,
            },
            "email": [
                {
                    "label":        a.label,
                    "enabled":      a.enabled,
                    "imap_host":    a.imap_host,
                    "imap_port":    a.imap_port,
                    "smtp_host":    a.smtp_host,
                    "smtp_port":    a.smtp_port,
                    "username":     a.username,
                    "password_set": bool(a.password),
                    "mailbox":      a.mailbox,
                }
                for a in cfg.emails
            ],
            "calendar": [
                {
                    "label":         a.label,
                    "enabled":       a.enabled,
                    "url":           a.url,
                    "username":      a.username,
                    "password_set":  bool(a.password),
                    "calendar_name": a.calendar_name,
                    "timezone":      a.timezone,
                }
                for a in cfg.calendars
            ],
            "transsion": {
                "email":         cfg.transsion.email,
                "display_name":  cfg.transsion.display_name,
                "access_token":  cfg.transsion.access_token,
                "authed":        bool(cfg.transsion.email and cfg.provider.api_key
                                      and cfg.provider.name == "transsion"),
            },
            "context": {
                "history_budget":        cfg.context.history_budget,
                "compact_threshold":     cfg.context.compact_threshold,
                "compact_keep_turns":    cfg.context.compact_keep_turns,
                "compact_strategy":      cfg.context.compact_strategy,
                "memory_min_score":      cfg.context.memory_min_score,
                "memory_max_tokens":     cfg.context.memory_max_tokens,
                "auto_extract":          cfg.context.auto_extract,
                "memory_decay_rate":     cfg.context.memory_decay_rate,
                "retrieval_temperature": cfg.context.retrieval_temperature,
                "serendipity_budget":    cfg.context.serendipity_budget,
            },
            "memory": {
                "embed_provider": cfg.memory.embed_provider,
                "embed_model":    cfg.memory.embed_model,
            },
            "skill_dir":      str(cfg.tools.skill_dir or ""),
            "user_skill_dir": str(cfg.tools.user_skill_dir or ""),
            "workspace_dir":  str(cfg.agent.workspace_dir or ""),
            "workspace": self._workspace_status(cfg),
            "workspaces": [
                {
                    "name":        ws.name,
                    "path":        ws.path,
                    "description": ws.description,
                }
                for ws in cfg.workspaces.list
            ],
            # Free-form API keys for skills/integrations.
            # Values masked: only set/unset exposed.
            "api_keys": {
                k: bool(v) for k, v in (cfg.api_keys or {}).items()
            },
        }

    def _workspace_status(self, cfg) -> dict:
        """Return workspace directory status for the setup wizard."""
        from pathlib import Path as _Path
        ws = cfg.agent.workspace_dir
        if ws is None:
            return {"configured": False, "path": "", "soul_md": False, "user_md": False}
        ws = _Path(ws)
        return {
            "configured": ws.is_dir(),
            "path": str(ws),
            "soul_md": (ws / "SOUL.md").exists(),
            "user_md": (ws / "USER.md").exists(),
        }

    # ── Config apply ───────────────────────────────────────────────────────────

    async def _handle_test_app_connector(self, ws, data: dict) -> None:
        target = str(data.get("target") or "").strip().lower()
        aliases = {"google-workspace": "google_workspace"}
        target = aliases.get(target, target)
        log.info("Testing app connector: %s", target or "(empty)")
        supported = {"github", "google_workspace", "notion", "jira", "reddit", "x"}
        if target not in supported:
            await ws.send(json.dumps({
                "type": "test_app_connector_result",
                "target": target,
                "ok": False,
                "message": f"Unknown app connector: {target or '(empty)'}",
            }))
            return
        from hushclaw.config.schema import (
            GitHubAppConnectorConfig, GoogleWorkspaceAppConnectorConfig,
            NotionAppConnectorConfig, JiraAppConnectorConfig,
            RedditAppConnectorConfig, XAppConnectorConfig,
        )
        from hushclaw.app_connectors.github import test_github_connection
        from hushclaw.app_connectors.google_workspace import test_google_workspace_connection
        from hushclaw.app_connectors.notion import test_notion_connection
        from hushclaw.app_connectors.jira import test_jira_connection
        from hushclaw.app_connectors.reddit import test_reddit_connection
        from hushclaw.app_connectors.x import test_x_connection
        from hushclaw.secrets import get_secret_store

        class _TestSecretStore:
            def __init__(self, base, values):
                self._base = base
                self._values = values

            def get(self, key, default=""):
                return self._values.get(key) or self._base.get(key, default)

            def set(self, key, value):
                key = str(key).strip()
                if not key:
                    return
                value = str(value)
                self._values[key] = value
                self._base.set(key, value)

        secrets = get_secret_store()
        transient = {}
        cfg_root = self._gateway.base_agent.config.app_connectors
        try:
            if target == "github":
                cfg = cfg_root.github
                token_ref = str(data.get("token_ref") or cfg.token_ref or "app_connectors.github.token").strip()
                test_cfg = GitHubAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "pat"),
                    client_id_ref=str(data.get("client_id_ref") or cfg.client_id_ref or "app_connectors.github.client_id").strip(),
                    client_secret_ref=str(data.get("client_secret_ref") or cfg.client_secret_ref or "app_connectors.github.client_secret").strip(),
                    token_ref=token_ref,
                    default_repo=str(data.get("default_repo") or cfg.default_repo or "").strip(),
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                token = str(data.get("token") or "").strip()
                if token:
                    transient[token_ref] = token
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_github_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
            elif target == "google_workspace":
                cfg = cfg_root.google_workspace
                test_cfg = GoogleWorkspaceAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "oauth"),
                    client_id_ref=str(data.get("client_id_ref") or cfg.client_id_ref),
                    client_secret_ref=str(data.get("client_secret_ref") or cfg.client_secret_ref),
                    access_token_ref=str(data.get("access_token_ref") or cfg.access_token_ref),
                    refresh_token_ref=str(data.get("refresh_token_ref") or cfg.refresh_token_ref),
                    scopes=data.get("scopes") if isinstance(data.get("scopes"), list) else cfg.scopes,
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                for value_key, ref_key in (
                    ("client_id", test_cfg.client_id_ref),
                    ("client_secret", test_cfg.client_secret_ref),
                    ("access_token", test_cfg.access_token_ref),
                    ("refresh_token", test_cfg.refresh_token_ref),
                ):
                    value = str(data.get(value_key) or "").strip()
                    if value:
                        transient[ref_key] = value
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_google_workspace_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
            elif target == "notion":
                cfg = cfg_root.notion
                token_ref = str(data.get("token_ref") or cfg.token_ref or "app_connectors.notion.token").strip()
                test_cfg = NotionAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "internal_token"),
                    client_id_ref=str(data.get("client_id_ref") or cfg.client_id_ref or "app_connectors.notion.client_id").strip(),
                    client_secret_ref=str(data.get("client_secret_ref") or cfg.client_secret_ref or "app_connectors.notion.client_secret").strip(),
                    token_ref=token_ref,
                    workspace_name=str(data.get("workspace_name") or cfg.workspace_name or "").strip(),
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                token = str(data.get("token") or "").strip()
                if token:
                    transient[token_ref] = token
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_notion_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
            elif target == "jira":
                cfg = cfg_root.jira
                token_ref = str(data.get("token_ref") or cfg.token_ref or "app_connectors.jira.token").strip()
                access_ref = str(data.get("access_token_ref") or cfg.access_token_ref or "app_connectors.jira.access_token").strip()
                refresh_ref = str(data.get("refresh_token_ref") or cfg.refresh_token_ref or "app_connectors.jira.refresh_token").strip()
                test_cfg = JiraAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "api_token"),
                    site_url=str(data.get("site_url") or cfg.site_url or "").strip(),
                    email=str(data.get("email") or cfg.email or "").strip(),
                    client_id_ref=str(data.get("client_id_ref") or cfg.client_id_ref or "app_connectors.jira.client_id").strip(),
                    client_secret_ref=str(data.get("client_secret_ref") or cfg.client_secret_ref or "app_connectors.jira.client_secret").strip(),
                    token_ref=token_ref,
                    access_token_ref=access_ref,
                    refresh_token_ref=refresh_ref,
                    cloud_id=str(data.get("cloud_id") or cfg.cloud_id or "").strip(),
                    scopes=data.get("scopes") if isinstance(data.get("scopes"), list) else cfg.scopes,
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                token = str(data.get("token") or "").strip()
                access_token = str(data.get("access_token") or "").strip()
                refresh_token = str(data.get("refresh_token") or "").strip()
                if token:
                    transient[token_ref] = token
                if access_token:
                    transient[access_ref] = access_token
                if refresh_token:
                    transient[refresh_ref] = refresh_token
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_jira_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
            elif target == "reddit":
                cfg = cfg_root.reddit
                access_ref = str(data.get("access_token_ref") or cfg.access_token_ref or "app_connectors.reddit.access_token").strip()
                refresh_ref = str(data.get("refresh_token_ref") or cfg.refresh_token_ref or "app_connectors.reddit.refresh_token").strip()
                test_cfg = RedditAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_mode=str(data.get("auth_mode") or cfg.auth_mode or "custom"),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "oauth"),
                    client_id_ref=str(data.get("client_id_ref") or cfg.client_id_ref or "app_connectors.reddit.client_id").strip(),
                    client_secret_ref=str(data.get("client_secret_ref") or cfg.client_secret_ref or "app_connectors.reddit.client_secret").strip(),
                    access_token_ref=access_ref,
                    refresh_token_ref=refresh_ref,
                    user_agent=str(data.get("user_agent") or cfg.user_agent or "HushClaw-AppConnector/1.0").strip(),
                    default_subreddit=str(data.get("default_subreddit") or cfg.default_subreddit or "").strip(),
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                for value_key, ref in (
                    ("client_id", test_cfg.client_id_ref),
                    ("client_secret", test_cfg.client_secret_ref),
                    ("access_token", access_ref),
                    ("refresh_token", refresh_ref),
                ):
                    value = str(data.get(value_key) or "").strip()
                    if value:
                        transient[ref] = value
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_reddit_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
            else:
                cfg = cfg_root.x
                bearer_ref = str(data.get("bearer_token_ref") or cfg.bearer_token_ref or "app_connectors.x.bearer_token").strip()
                access_ref = str(data.get("access_token_ref") or cfg.access_token_ref or "app_connectors.x.access_token").strip()
                refresh_ref = str(data.get("refresh_token_ref") or cfg.refresh_token_ref or "app_connectors.x.refresh_token").strip()
                test_cfg = XAppConnectorConfig(
                    enabled=bool(data.get("enabled", cfg.enabled)),
                    auth_mode=str(data.get("auth_mode") or cfg.auth_mode or "custom"),
                    auth_type=str(data.get("auth_type") or cfg.auth_type or "app_keys"),
                    consumer_key_ref=str(
                        data.get("consumer_key_ref")
                        or data.get("client_id_ref")
                        or cfg.consumer_key_ref
                        or "app_connectors.x.consumer_key"
                    ).strip(),
                    consumer_secret_ref=str(
                        data.get("consumer_secret_ref")
                        or data.get("client_secret_ref")
                        or cfg.consumer_secret_ref
                        or "app_connectors.x.consumer_secret"
                    ).strip(),
                    oauth_client_id_ref=str(data.get("oauth_client_id_ref") or cfg.oauth_client_id_ref or "app_connectors.x.oauth_client_id").strip(),
                    oauth_client_secret_ref=str(data.get("oauth_client_secret_ref") or cfg.oauth_client_secret_ref or "app_connectors.x.oauth_client_secret").strip(),
                    bearer_token_ref=bearer_ref,
                    access_token_ref=access_ref,
                    refresh_token_ref=refresh_ref,
                    stream_enabled=bool(data.get("stream_enabled", cfg.stream_enabled)),
                    stream_rules=data.get("stream_rules") if isinstance(data.get("stream_rules"), list) else cfg.stream_rules,
                    require_publish_confirmation=(
                        bool(data["require_publish_confirmation"])
                        if "require_publish_confirmation" in data
                        else cfg.require_publish_confirmation
                    ),
                    allow_actions=bool(data.get("allow_actions", cfg.allow_actions)),
                )
                for value_key, ref in (
                    ("consumer_key", test_cfg.consumer_key_ref),
                    ("consumer_secret", test_cfg.consumer_secret_ref),
                    ("oauth_client_id", test_cfg.oauth_client_id_ref),
                    ("oauth_client_secret", test_cfg.oauth_client_secret_ref),
                    ("bearer_token", bearer_ref),
                    ("access_token", access_ref),
                    ("refresh_token", refresh_ref),
                ):
                    legacy_key = "client_id" if value_key == "consumer_key" else "client_secret" if value_key == "consumer_secret" else value_key
                    value = str(data.get(value_key) or data.get(legacy_key) or "").strip()
                    if value:
                        transient[ref] = value
                result = await asyncio.wait_for(
                    asyncio.to_thread(test_x_connection, test_cfg, _TestSecretStore(secrets, transient)),
                    timeout=25,
                )
        except asyncio.TimeoutError:
            result = {"ok": False, "message": f"{target} connection test timed out."}
        except Exception as exc:
            log.error("test_app_connector failed target=%s: %s", target, exc, exc_info=True)
            result = {"ok": False, "message": str(exc)}
        await ws.send(json.dumps({
            "type": "test_app_connector_result",
            "target": target,
            **result,
        }))

    def _apply_config(self) -> None:
        """Hot-reload provider and config on the running agent after a config save."""
        try:
            from hushclaw.config.loader import load_config
            new_cfg = load_config()
            from hushclaw.update import UpdateService
            agent = self._gateway.base_agent
            agent.reload_runtime(new_cfg)
            # Keep gateway._config in sync so new dynamic agents (created via
            # create_agent tool) inherit the updated provider, not the stale
            # startup config.
            self._gateway._config = new_cfg
            # Update provider on all already-registered dynamic agent pools so
            # they immediately use the new provider without requiring a restart.
            # Reuse the instance already created by reload_runtime() — all pools
            # share the same [provider] config so one object is sufficient.
            for _name, _pool in self._gateway._pools.items():
                if _name != "default":
                    _pool._agent.provider = agent.provider
            # Flush all cached AgentLoop sessions so the next request creates a
            # fresh loop bound to the new provider/config (old loops hold a
            # reference to the previous provider object and would keep using it).
            self._gateway.clear_all_cached_loops()
            self._update_service = UpdateService(
                cache_ttl_seconds=max(60, int(new_cfg.update.cache_ttl_seconds or 900)),
            )
            log.info(
                "Config reloaded: provider=%s model=%s (session cache flushed)",
                new_cfg.provider.name, new_cfg.agent.model,
            )
            # Reload connectors so enabling/disabling a channel takes effect
            # without a server restart.  Scheduled as a task because _apply_config
            # is synchronous but connector start/stop is async.
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self._connectors.reload(
                            new_cfg.connectors,
                            self._gateway,
                            webhook_registry=self._webhook_handlers,
                            calendar_config=new_cfg.calendar,
                            memory_store=self._gateway.memory,
                        ),
                        name="connectors-reload",
                    )
            except Exception as conn_exc:
                log.error("Connector reload scheduling error: %s", conn_exc)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self._app_connector_runtime.reload(new_cfg.app_connectors, self._gateway.memory),
                        name="app-connectors-reload",
                    )
            except Exception as app_conn_exc:
                log.error("App Connector runtime reload scheduling error: %s", app_conn_exc)
        except Exception as exc:
            log.error("Config reload error: %s", exc, exc_info=True)

    async def _handle_list_app_inbox_events(self, ws, data: dict) -> None:
        connector_id = str(data.get("connector_id") or data.get("connector") or "").strip()
        status = str(data.get("status") or "").strip()
        limit = max(1, min(int(data.get("limit") or 50), 200))
        offset = max(0, int(data.get("offset") or 0))
        items = self._gateway.memory.list_app_inbox_events(
            connector_id=connector_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        await self._send_json(ws, {
            "type": "app_inbox_events",
            "items": items,
            "connector_id": connector_id,
            "status": status,
            "offset": offset,
            "limit": limit,
        })

    async def _handle_update_app_inbox_event(self, ws, data: dict) -> None:
        event_id = str(data.get("event_id") or "").strip()
        status = str(data.get("status") or "").strip()
        item = self._gateway.memory.update_app_inbox_event_status(event_id, status)
        await self._send_json(ws, {
            "type": "app_inbox_event_updated",
            "ok": item is not None,
            "item": item,
            "event_id": event_id,
        })

    async def _handle_publish_app_connector_draft(self, ws, data: dict) -> None:
        connector_id = str(data.get("connector_id") or data.get("connector") or "").strip()
        event_id = str(data.get("event_id") or "").strip()
        log.info("Publishing app connector draft: connector=%s event_id=%s", connector_id or "(empty)", event_id or "(empty)")
        if connector_id != "x":
            await self._send_json(ws, {
                "type": "app_connector_draft_published",
                "ok": False,
                "event_id": event_id,
                "message": f"Publishing drafts is not supported for {connector_id or '(empty)'}.",
            })
            return
        await self._send_json(ws, {
            "type": "app_connector_draft_publish_progress",
            "event_id": event_id,
            "message": "Publish request received.",
        })

        from hushclaw.app_connectors.x import load_publishable_draft, mark_draft_published, publish_loaded_draft
        from hushclaw.secrets import get_secret_store

        try:
            log.info("Loading X draft before publish: event_id=%s", event_id)
            draft = load_publishable_draft(self._gateway.memory, event_id)
            if not draft.get("ok"):
                log.warning("X draft not publishable: event_id=%s message=%s", event_id, draft.get("message"))
                result = {"ok": False, "message": draft.get("message") or "Draft is not publishable."}
            else:
                await self._send_json(ws, {
                    "type": "app_connector_draft_publish_progress",
                    "event_id": event_id,
                    "message": "Calling X API...",
                })
                log.info(
                    "Calling X API for draft publish: event_id=%s action=%s text_len=%s",
                    event_id,
                    draft.get("action"),
                    len(str(draft.get("text") or "")),
                )
                publish_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        publish_loaded_draft,
                        self._gateway.base_agent.config.app_connectors.x,
                        get_secret_store(),
                        draft,
                    ),
                    timeout=35,
                )
                log.info(
                    "X API draft publish returned: event_id=%s ok=%s",
                    event_id,
                    not publish_result.is_error,
                )
                if publish_result.is_error:
                    log.warning(
                        "X API draft publish failed: event_id=%s message=%s",
                        event_id,
                        publish_result.content,
                    )
                result = mark_draft_published(self._gateway.memory, event_id, publish_result)
                log.info(
                    "X draft publish finished: event_id=%s ok=%s message=%s",
                    event_id,
                    result.get("ok"),
                    result.get("message") or "",
                )
        except asyncio.TimeoutError:
            log.error("publish_app_connector_draft timed out connector=%s event_id=%s", connector_id, event_id)
            result = {"ok": False, "message": "Publishing draft timed out while calling X API."}
        except Exception as exc:
            log.error(
                "publish_app_connector_draft failed connector=%s event_id=%s: %s",
                connector_id,
                event_id,
                exc,
                exc_info=True,
            )
            result = {"ok": False, "message": str(exc)}
        await self._send_json(ws, {
            "type": "app_connector_draft_published",
            "event_id": event_id,
            **result,
        })

    # ── List models ────────────────────────────────────────────────────────────

    async def _handle_list_models(self, ws, data: dict) -> None:
        from hushclaw.config.schema import ProviderConfig
        from hushclaw.providers.registry import get_provider
        base_cfg = self._gateway.base_agent.config.provider
        provider_name = data.get("provider") or base_cfg.name

        # Transsion: model list lives on the control plane (bus-ie), not the AI
        # Router (airouter).  Use the stored access_token to call the same
        # /oneapi/api-credentials/info endpoint that acquire_credentials uses.
        # Prefer the token from the WS message (set before Save) over the one
        # in config (only available after Save).
        if provider_name == "transsion":
            import functools
            from hushclaw.providers.transsion import get_models_from_credentials
            access_token = (
                data.get("access_token") or
                self._gateway.base_agent.config.transsion.access_token
            )
            try:
                models = await asyncio.get_event_loop().run_in_executor(
                    None,
                    functools.partial(get_models_from_credentials, access_token),
                )
                await ws.send(json.dumps({"type": "models", "items": models}))
            except Exception as e:
                log.warning("transsion list_models from control plane failed: %s", e)
                await ws.send(json.dumps({"type": "models", "items": [], "error": str(e)}))
            return

        cfg = ProviderConfig(
            name=provider_name,
            api_key=data.get("api_key") or base_cfg.api_key,
            base_url=data.get("base_url") or base_cfg.base_url,
        )
        try:
            provider = get_provider(cfg)
            models = await provider.list_models()
            await ws.send(json.dumps({"type": "models", "items": models}))
        except Exception as e:
            await ws.send(json.dumps({"type": "models", "items": [], "error": str(e)}))

    # ── Config / workspace handler delegators ──────────────────────────────────

    async def _handle_init_workspace(self, ws, data: dict) -> None:
        await config_handler.handle_init_workspace(ws, data, self._gateway)

    async def _handle_save_config(self, ws, data: dict) -> None:
        await config_handler.handle_save_config(ws, data, self._apply_config)

    async def _handle_save_update_policy(self, ws, data: dict) -> None:
        await config_handler.handle_save_update_policy(ws, data, self._apply_config)

    # ── Transsion / TEX AI Router auth delegators ─────────────────────────────

    async def _handle_transsion_send_code(self, ws, data: dict) -> None:
        await transsion_handler.handle_send_code(ws, data)

    async def _handle_transsion_login(self, ws, data: dict) -> None:
        await transsion_handler.handle_login(ws, data)

    async def _handle_transsion_quota(self, ws, data: dict) -> None:
        await transsion_handler.handle_quota(ws, data, self._gateway)

    # ── Update handler delegators ──────────────────────────────────────────────

    async def _handle_check_update(self, ws, data: dict) -> None:
        await update_handler.handle_check_update(ws, data, self._gateway, self._update_service)

    async def _handle_run_update(self, ws, data: dict) -> None:
        if not hasattr(self, "_upgrade_state") or not isinstance(self._upgrade_state, dict):
            self._upgrade_state = {"in_progress": bool(getattr(self, "_upgrade_in_progress", False))}
        await update_handler.handle_run_update(
            ws, data, self._gateway, self._update_executor,
            self._upgrade_lock, self._upgrade_state,
            self._running_sessions, self._connected_clients,
        )
        self._upgrade_in_progress = self._upgrade_state["in_progress"]

    # ── Provider / skill handler delegators ───────────────────────────────────

    async def _handle_test_provider(self, ws, data: dict) -> None:
        await provider_handler.handle_test_provider(ws, data, self._gateway)

    async def _handle_list_skills(self, ws, data: dict | None = None) -> None:
        await skill_handler.handle_list_skills(ws, self._gateway, data)

    async def _handle_save_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_save_skill(ws, data, self._gateway)

    async def _handle_delete_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_delete_skill(ws, data, self._gateway)

    async def _handle_get_skill_detail(self, ws, data: dict) -> None:
        await skill_handler.handle_get_skill_detail(ws, data, self._gateway)

    async def _handle_get_agent_runtime_status(self, ws, data: dict) -> None:
        await skill_handler.handle_get_agent_runtime_status(ws, data, self._gateway)

    async def _handle_check_skills_health(self, ws) -> None:
        await skill_handler.handle_check_skills_health(ws, self._gateway)

    async def _handle_set_skill_enabled(self, ws, data: dict) -> None:
        await skill_handler.handle_set_skill_enabled(ws, data, self._gateway)

    async def _handle_install_skill_repo(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_repo(ws, data, self._gateway)

    async def _handle_install_skill_zip(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_zip(ws, data, self._gateway)

    async def _handle_export_skills(self, ws, data: dict) -> None:
        await skill_handler.handle_export_skills(ws, data, self._gateway)

    async def _handle_import_skill_zip_upload(self, ws, data: dict) -> None:
        await skill_handler.handle_import_skill_zip(ws, data, self._gateway)

    async def _handle_test_email(self, ws, data: dict) -> None:
        await integration_handler.handle_test_email(ws, data, self._gateway)

    async def _handle_test_calendar(self, ws, data: dict) -> None:
        await integration_handler.handle_test_calendar(ws, data, self._gateway)
