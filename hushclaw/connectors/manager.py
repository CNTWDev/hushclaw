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
    ) -> None:
        self._connectors: dict[str, Connector] = {}
        self._webhook_registry: dict = webhook_registry or {}
        self._build(config, gateway, self._webhook_registry)

    def _build(
        self,
        config: ConnectorsConfig,
        gateway,
        webhooks: dict,
    ) -> None:
        """Instantiate connectors from config (does not start them)."""
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

    async def start(self) -> None:
        for connector in self._connectors.values():
            await connector.start()

    async def stop(self) -> None:
        for connector in self._connectors.values():
            await connector.stop()

    def status(self) -> dict[str, bool]:
        """Return {platform_id: is_connected} for all configured connectors."""
        return {name: c.connected for name, c in self._connectors.items()}

    async def reload(
        self,
        config: ConnectorsConfig,
        gateway,
        webhook_registry: dict | None = None,
    ) -> None:
        """Stop all running connectors and restart with updated config.

        Called by the server after a hot-reload so that enabling/disabling
        a channel in the wizard takes effect immediately without a restart.
        """
        log.info("[connectors] reloading connectors after config change")
        await self.stop()
        self._connectors.clear()
        self._build(config, gateway, webhook_registry or self._webhook_registry)
        await self.start()
        log.info("[connectors] connector reload complete (%d active)", len(self._connectors))
