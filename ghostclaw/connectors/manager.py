"""ConnectorsManager — lifecycle manager for all enabled connectors."""
from __future__ import annotations

from ghostclaw.connectors.base import Connector, log
from ghostclaw.config.schema import ConnectorsConfig


class ConnectorsManager:
    """Starts and stops all configured connectors alongside the GhostClaw server."""

    def __init__(self, config: ConnectorsConfig, gateway) -> None:
        self._connectors: list[Connector] = []

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

    async def start(self) -> None:
        for connector in self._connectors:
            await connector.start()

    async def stop(self) -> None:
        for connector in self._connectors:
            await connector.stop()
