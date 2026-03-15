"""ConnectorsManager — lifecycle manager for all enabled connectors."""
from __future__ import annotations

from ghostclaw.connectors.base import Connector, log
from ghostclaw.config.schema import ConnectorsConfig


class ConnectorsManager:
    """Starts and stops all configured connectors alongside the GhostClaw server."""

    def __init__(
        self,
        config: ConnectorsConfig,
        gateway,
        webhook_registry: dict | None = None,
    ) -> None:
        self._connectors: list[Connector] = []
        _webhooks = webhook_registry or {}

        tg = config.telegram
        if tg.enabled and tg.bot_token:
            from ghostclaw.connectors.telegram import TelegramConnector
            self._connectors.append(TelegramConnector(gateway, tg))
            log.info("[connectors] Telegram connector enabled")

        fs = config.feishu
        if fs.enabled and fs.app_id and fs.app_secret:
            from ghostclaw.connectors.feishu import FeishuConnector
            self._connectors.append(FeishuConnector(gateway, fs))
            log.info("[connectors] Feishu connector enabled")

        dc = config.discord
        if dc.enabled and dc.bot_token:
            from ghostclaw.connectors.discord import DiscordConnector
            self._connectors.append(DiscordConnector(gateway, dc))
            log.info("[connectors] Discord connector enabled")

        sl = config.slack
        if sl.enabled and sl.bot_token and sl.app_token:
            from ghostclaw.connectors.slack import SlackConnector
            self._connectors.append(SlackConnector(gateway, sl))
            log.info("[connectors] Slack connector enabled")

        dt = config.dingtalk
        if dt.enabled and dt.client_id and dt.client_secret:
            from ghostclaw.connectors.dingtalk import DingTalkConnector
            self._connectors.append(DingTalkConnector(gateway, dt))
            log.info("[connectors] DingTalk connector enabled")

        wc = config.wecom
        if wc.enabled and wc.corp_id and wc.corp_secret:
            from ghostclaw.connectors.wecom import WeChatWorkConnector
            self._connectors.append(WeChatWorkConnector(gateway, wc, _webhooks))
            log.info("[connectors] WeCom connector enabled (webhook: POST /webhook/wecom)")

    async def start(self) -> None:
        for connector in self._connectors:
            await connector.start()

    async def stop(self) -> None:
        for connector in self._connectors:
            await connector.stop()
