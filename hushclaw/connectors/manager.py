"""ConnectorsManager — lifecycle manager for all enabled connectors."""
from __future__ import annotations

from hushclaw.connectors.base import Connector, log
from hushclaw.config.schema import ConnectorsConfig


class ConnectorsManager:
    """Starts and stops all configured connectors alongside the HushClaw server."""

    def __init__(
        self,
        config: ConnectorsConfig,
        gateway,
        webhook_registry: dict | None = None,
        calendar_config=None,   # CalendarConfig | None
        memory_store=None,      # MemoryStore | None
        google_workspace_config=None,
    ) -> None:
        self._connectors: dict[str, Connector] = {}
        self._webhook_registry: dict = webhook_registry or {}
        self._caldav_sync = None
        self._google_calendar_sync = None
        self._build(config, gateway, self._webhook_registry)
        if calendar_config is not None and memory_store is not None:
            self._init_caldav_sync(calendar_config, memory_store)
        if google_workspace_config is not None and memory_store is not None:
            self._init_google_calendar_sync(google_workspace_config, memory_store)

    def _build(
        self,
        config: ConnectorsConfig,
        gateway,
        webhooks: dict,
    ) -> None:
        """Instantiate connectors from config (does not start them)."""
        os_api = getattr(gateway, "_os_api", None)
        clear_senders = getattr(os_api, "clear_delivery_senders", None)
        if callable(clear_senders):
            clear_senders()
        tg = config.telegram
        if tg.enabled and tg.bot_token:
            from hushclaw.connectors.telegram import TelegramConnector
            self._connectors["telegram"] = TelegramConnector(gateway, tg)
            log.info("[connectors] Telegram connector enabled")

        fs = config.feishu
        if fs.enabled and fs.app_id and fs.app_secret:
            from hushclaw.connectors.feishu import FeishuConnector
            self._connectors["feishu"] = FeishuConnector(gateway, fs)
            log.info("[connectors] Feishu connector enabled")

        dc = config.discord
        if dc.enabled and dc.bot_token:
            from hushclaw.connectors.discord import DiscordConnector
            self._connectors["discord"] = DiscordConnector(gateway, dc)
            log.info("[connectors] Discord connector enabled")

        sl = config.slack
        if sl.enabled and sl.bot_token and sl.app_token:
            from hushclaw.connectors.slack import SlackConnector
            self._connectors["slack"] = SlackConnector(gateway, sl)
            log.info("[connectors] Slack connector enabled")

        dt = config.dingtalk
        if dt.enabled and dt.client_id and dt.client_secret:
            from hushclaw.connectors.dingtalk import DingTalkConnector
            self._connectors["dingtalk"] = DingTalkConnector(gateway, dt)
            log.info("[connectors] DingTalk connector enabled")

        wc = config.wecom
        if wc.enabled and wc.corp_id and wc.corp_secret:
            from hushclaw.connectors.wecom import WeChatWorkConnector
            self._connectors["wecom"] = WeChatWorkConnector(gateway, wc, webhooks)
            log.info("[connectors] WeCom connector enabled (webhook: POST /webhook/wecom)")

        wa = config.whatsapp
        if wa.enabled and wa.account_sid and wa.auth_token and wa.from_number:
            from hushclaw.connectors.whatsapp import WhatsAppConnector
            self._connectors["whatsapp"] = WhatsAppConnector(gateway, wa, webhooks)
            log.info("[connectors] WhatsApp connector enabled (webhook: POST /webhook/whatsapp)")

    def _init_caldav_sync(self, calendar_config, memory_store) -> None:
        if not calendar_config.enabled:
            log.info("[connectors] CalDAV sync skipped: calendar.enabled=false in config")
            return
        if not calendar_config.url:
            log.warning("[connectors] CalDAV sync skipped: calendar.url not set in config")
            return
        from hushclaw.connectors.caldav_sync import CalDAVSyncService
        self._caldav_sync = CalDAVSyncService(calendar_config, memory_store)
        log.info(
            "[connectors] CalDAV sync service enabled (url=%s, user=%s)",
            calendar_config.url,
            calendar_config.username or "(none)",
        )

    def _init_google_calendar_sync(self, config, memory_store) -> None:
        if not config.enabled or not config.calendar_sync_enabled:
            return
        from hushclaw.connectors.google_calendar_sync import GoogleCalendarSyncService
        from hushclaw.secrets import get_secret_store
        self._google_calendar_sync = GoogleCalendarSyncService(config, memory_store, get_secret_store())

    async def start(self) -> None:
        for connector in self._connectors.values():
            await connector.start()
        if self._caldav_sync is not None:
            await self._caldav_sync.start()
        if self._google_calendar_sync is not None:
            await self._google_calendar_sync.start()

    async def stop(self) -> None:
        if self._google_calendar_sync is not None:
            await self._google_calendar_sync.stop()
        if self._caldav_sync is not None:
            await self._caldav_sync.stop()
        for connector in self._connectors.values():
            await connector.stop()

    def status(self) -> dict[str, bool]:
        """Return {platform_id: is_connected} for all configured connectors."""
        return {name: c.connected for name, c in self._connectors.items()}

    async def force_caldav_sync(self) -> int:
        """Refresh configured CalDAV and Google sources for the Calendar view."""
        services = [service for service in (self._caldav_sync, self._google_calendar_sync) if service is not None]
        if not services:
            log.warning("[connectors] force_caldav_sync called but CalDAV sync service is not initialised — check calendar.enabled and calendar.url in config")
            return 0
        count = 0
        errors = []
        for service in services:
            count += await service.sync()
            if service.last_error:
                errors.append(service.last_error)
        if errors:
            raise RuntimeError("; ".join(errors))
        return count

    @property
    def caldav_last_sync(self) -> float:
        """Most recent successful calendar source sync (0 if never / disabled)."""
        return max((service.last_sync for service in (self._caldav_sync, self._google_calendar_sync)
                    if service is not None), default=0.0)

    async def reload(
        self,
        config: ConnectorsConfig,
        gateway,
        webhook_registry: dict | None = None,
        calendar_config=None,
        memory_store=None,
        google_workspace_config=None,
    ) -> None:
        """Stop all running connectors and restart with updated config."""
        log.info("[connectors] reloading connectors after config change")
        await self.stop()
        self._connectors.clear()
        self._caldav_sync = None
        self._google_calendar_sync = None
        self._build(config, gateway, webhook_registry or self._webhook_registry)
        if calendar_config is not None and memory_store is not None:
            self._init_caldav_sync(calendar_config, memory_store)
        if google_workspace_config is not None and memory_store is not None:
            self._init_google_calendar_sync(google_workspace_config, memory_store)
        await self.start()
        log.info("[connectors] connector reload complete (%d active)", len(self._connectors))
