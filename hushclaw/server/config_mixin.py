"""server/config_mixin.py — config status, apply, playwright check, list models,
and thin delegators to provider/skill/transsion/update/config handlers.

Extracted from server_impl.py. All methods are accessed via self (mixin pattern).
"""
from __future__ import annotations

import asyncio
import json

from hushclaw.server import provider_handler, skill_handler, transsion_handler, config_handler, update_handler
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

        api_key_masked = ""
        if api_key:
            api_key_masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) > 8 else "set"

        from hushclaw.config.loader import get_config_dir
        cfg_file = str(get_config_dir() / "hushclaw.toml")

        c   = cfg.connectors
        tg  = c.telegram
        fs  = c.feishu
        dc  = c.discord
        sl  = c.slack
        dt  = c.dingtalk
        wc  = c.wecom
        upd = cfg.update
        last_update = self._update_service.last_result or {}
        return {
            "type": "config_status",
            "version":    self._update_service.current_version,
            "build_time": _BUILD_TIME,
            "configured": (not needs_key) or bool(api_key),
            "provider": provider,
            "model": cfg.agent.model,
            "base_url": cfg.provider.base_url or "",
            "public_base_url": cfg.server.public_base_url or "",
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "max_tokens": cfg.agent.max_tokens,
            "cheap_model": cfg.agent.cheap_model or "",
            "max_tool_rounds": cfg.agent.max_tool_rounds,
            "system_prompt": cfg.agent.system_prompt,
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
            "browser": {
                "enabled":                cfg.browser.enabled,
                "headless":               cfg.browser.headless,
                "timeout":                cfg.browser.timeout,
                "playwright_installed":   self._check_playwright(),
                "use_user_chrome":        bool(cfg.browser.remote_debugging_url),
                "remote_debugging_url":   cfg.browser.remote_debugging_url,
            },
            "email": {
                "enabled":      cfg.email.enabled,
                "imap_host":    cfg.email.imap_host,
                "imap_port":    cfg.email.imap_port,
                "smtp_host":    cfg.email.smtp_host,
                "smtp_port":    cfg.email.smtp_port,
                "username":     cfg.email.username,
                "password_set": bool(cfg.email.password),
                "mailbox":      cfg.email.mailbox,
            },
            "calendar": {
                "enabled":       cfg.calendar.enabled,
                "url":           cfg.calendar.url,
                "username":      cfg.calendar.username,
                "password_set":  bool(cfg.calendar.password),
                "calendar_name": cfg.calendar.calendar_name,
            },
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

    def _apply_config(self) -> None:
        """Hot-reload provider and config on the running agent after a config save."""
        try:
            from hushclaw.config.loader import load_config
            new_cfg = load_config()
            from hushclaw.providers.registry import get_provider
            from hushclaw.update import UpdateService
            agent = self._gateway.base_agent
            agent.reload_runtime(new_cfg)
            # Keep gateway._config in sync so new dynamic agents (created via
            # create_agent tool) inherit the updated provider, not the stale
            # startup config.
            self._gateway._config = new_cfg
            # Update provider on all already-registered dynamic agent pools so
            # they immediately use the new provider without requiring a restart.
            for _name, _pool in self._gateway._pools.items():
                if _name != "default":
                    _pool._agent.provider = get_provider(new_cfg.provider)
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
                        ),
                        name="connectors-reload",
                    )
            except Exception as conn_exc:
                log.error("Connector reload scheduling error: %s", conn_exc)
        except Exception as exc:
            log.error("Config reload error: %s", exc, exc_info=True)

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

    async def _handle_list_skills(self, ws) -> None:
        await skill_handler.handle_list_skills(ws, self._gateway)

    async def _handle_save_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_save_skill(ws, data, self._gateway)

    async def _handle_delete_skill(self, ws, data: dict) -> None:
        await skill_handler.handle_delete_skill(ws, data, self._gateway)

    async def _handle_install_skill_repo(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_repo(ws, data, self._gateway)

    async def _handle_install_skill_zip(self, ws, data: dict) -> None:
        await skill_handler.handle_install_skill_zip(ws, data, self._gateway)

    async def _handle_export_skills(self, ws, data: dict) -> None:
        await skill_handler.handle_export_skills(ws, data, self._gateway)

    async def _handle_import_skill_zip_upload(self, ws, data: dict) -> None:
        await skill_handler.handle_import_skill_zip(ws, data, self._gateway)
