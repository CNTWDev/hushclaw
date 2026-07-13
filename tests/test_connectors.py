"""Tests for the connectors package."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from types import SimpleNamespace

from hushclaw.config.schema import (
    ConnectorsConfig,
    FeishuConfig,
    TelegramConfig,
    WhatsAppConfig,
)
from hushclaw.connectors.manager import ConnectorsManager
from hushclaw.os_api import AgentOSService


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestConfigParsing:
    def test_defaults(self):
        cfg = ConnectorsConfig()
        assert cfg.telegram.enabled is False
        assert cfg.telegram.bot_token == ""
        assert cfg.telegram.agent == "default"
        assert cfg.telegram.allowlist == []
        assert cfg.telegram.polling_timeout == 30

        assert cfg.feishu.enabled is False
        assert cfg.feishu.app_id == ""
        assert cfg.feishu.app_secret == ""
        assert cfg.feishu.agent == "default"
        assert cfg.feishu.allowlist == []

    def test_telegram_from_dict(self):
        tg = TelegramConfig(
            enabled=True,
            bot_token="123:ABC",
            agent="my_agent",
            allowlist=[111, 222],
            polling_timeout=60,
        )
        assert tg.enabled is True
        assert tg.bot_token == "123:ABC"
        assert tg.allowlist == [111, 222]

    def test_feishu_from_dict(self):
        fs = FeishuConfig(
            enabled=True,
            app_id="cli_xxx",
            app_secret="secret",
            allowlist=["oc_abc"],
        )
        assert fs.enabled is True
        assert fs.app_id == "cli_xxx"
        assert fs.allowlist == ["oc_abc"]


# ---------------------------------------------------------------------------
# Session mapping
# ---------------------------------------------------------------------------

class TestSessionMapping:
    def _make_connector(self):
        from hushclaw.connectors.telegram import TelegramConnector
        cfg = TelegramConfig(enabled=True, bot_token="tok", agent="default")
        gw = MagicMock()
        gw.memory = SimpleNamespace(conn=None)
        # event_stream returns an empty async generator
        async def _empty_stream(*a, **kw):
            return
            yield  # make it an async generator
        gw.event_stream = _empty_stream
        gw._os_api = AgentOSService(gw)
        connector = TelegramConnector(gw, cfg)
        return connector

    @pytest.mark.asyncio
    async def test_connector_passes_client_now_to_gateway(self):
        from hushclaw.connectors.telegram import TelegramConnector

        captured: dict = {}

        async def _stream(*args, **kwargs):
            captured.update(kwargs)
            yield {"type": "done", "text": "ok"}

        cfg = TelegramConfig(enabled=True, bot_token="tok", agent="default")
        gw = MagicMock()
        gw.memory = SimpleNamespace(conn=None)
        gw.event_stream = _stream
        gw._os_api = AgentOSService(gw)
        connector = TelegramConnector(gw, cfg)
        connector._send_reply = AsyncMock()

        await connector._handle_message("chat_1", "hello")

        assert captured["workspace"] is None
        assert captured["client_now"].endswith("Z")
        assert "T" in captured["client_now"]
        connector._send_reply.assert_awaited_once_with("chat_1", "ok")


# ---------------------------------------------------------------------------
# Telegram allowlist filtering
# ---------------------------------------------------------------------------

class TestTelegramAllowlist:
    def _make_connector(self, allowlist: list[int]):
        from hushclaw.connectors.telegram import TelegramConnector
        cfg = TelegramConfig(
            enabled=True, bot_token="tok", agent="default", allowlist=allowlist
        )
        gw = MagicMock()
        connector = TelegramConnector(gw, cfg)
        return connector

    def test_empty_allowlist_accepts_all(self):
        c = self._make_connector([])
        # No filtering means any user_id passes
        user_id = 99999
        assert c._allowlist == []
        # Simulate the check in _poll_loop
        allowed = not c._allowlist or user_id in c._allowlist
        assert allowed is True

    def test_nonempty_allowlist_blocks_unlisted(self):
        c = self._make_connector([111, 222])
        user_id = 999
        allowed = not c._allowlist or user_id in c._allowlist
        assert allowed is False

    def test_nonempty_allowlist_permits_listed(self):
        c = self._make_connector([111, 222])
        user_id = 111
        allowed = not c._allowlist or user_id in c._allowlist
        assert allowed is True


# ---------------------------------------------------------------------------
# ConnectorsManager — no connectors when disabled / no token
# ---------------------------------------------------------------------------

class TestConnectorsManager:
    def _make_gateway(self):
        gw = MagicMock()
        return gw

    def test_no_connectors_when_disabled(self):
        cfg = ConnectorsConfig()  # all disabled by default
        mgr = ConnectorsManager(cfg, self._make_gateway())
        assert mgr._connectors == {}

    def test_telegram_connector_created_when_enabled(self):
        cfg = ConnectorsConfig(
            telegram=TelegramConfig(enabled=True, bot_token="tok")
        )
        mgr = ConnectorsManager(cfg, self._make_gateway())
        assert len(mgr._connectors) == 1
        from hushclaw.connectors.telegram import TelegramConnector
        assert isinstance(mgr._connectors["telegram"], TelegramConnector)

    def test_feishu_connector_created_when_enabled(self):
        cfg = ConnectorsConfig(
            feishu=FeishuConfig(enabled=True, app_id="cli_x", app_secret="sec")
        )
        mgr = ConnectorsManager(cfg, self._make_gateway())
        assert len(mgr._connectors) == 1
        from hushclaw.connectors.feishu import FeishuConnector
        assert isinstance(mgr._connectors["feishu"], FeishuConnector)

    def test_feishu_skipped_when_no_secret(self):
        cfg = ConnectorsConfig(
            feishu=FeishuConfig(enabled=True, app_id="cli_x", app_secret="")
        )
        mgr = ConnectorsManager(cfg, self._make_gateway())
        assert mgr._connectors == {}

    def test_whatsapp_connector_created_when_enabled(self):
        cfg = ConnectorsConfig(
            whatsapp=WhatsAppConfig(
                enabled=True,
                account_sid="AC123",
                auth_token="token",
                from_number="whatsapp:+14155238886",
            )
        )
        mgr = ConnectorsManager(cfg, self._make_gateway(), webhook_registry={})
        assert len(mgr._connectors) == 1
        from hushclaw.connectors.whatsapp import WhatsAppConnector
        assert isinstance(mgr._connectors["whatsapp"], WhatsAppConnector)

    def test_whatsapp_skipped_without_from_number(self):
        cfg = ConnectorsConfig(
            whatsapp=WhatsAppConfig(
                enabled=True,
                account_sid="AC123",
                auth_token="token",
                from_number="",
            )
        )
        mgr = ConnectorsManager(cfg, self._make_gateway(), webhook_registry={})
        assert mgr._connectors == {}

    @pytest.mark.asyncio
    async def test_start_stop_empty_manager(self):
        cfg = ConnectorsConfig()
        mgr = ConnectorsManager(cfg, self._make_gateway())
        await mgr.start()
        await mgr.stop()  # should not raise
