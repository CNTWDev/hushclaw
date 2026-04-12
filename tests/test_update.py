"""Tests for update check and upgrade orchestration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from hushclaw.server import HushClawServer
from hushclaw.update.provider import ReleaseInfo
from hushclaw.update.service import UpdateService, compare_versions


@dataclass
class _FakeProvider:
    calls: int = 0

    async def fetch_latest(self, include_prerelease: bool = False) -> ReleaseInfo:
        self.calls += 1
        suffix = "-rc1" if include_prerelease else ""
        return ReleaseInfo(
            version=f"v1.3.0{suffix}",
            html_url="https://example.com/release/v1.3.0",
            published_at="2026-03-01T00:00:00Z",
            prerelease=include_prerelease,
        )


def test_compare_versions_basic():
    assert compare_versions("v1.2.3", "1.2.4") == -1
    assert compare_versions("1.2.3", "1.2.3") == 0
    assert compare_versions("1.2.4", "v1.2.3") == 1


def test_compare_versions_prerelease_order():
    assert compare_versions("1.2.3-rc1", "1.2.3") == -1
    assert compare_versions("1.2.3", "1.2.3-rc1") == 1


def test_compare_versions_invalid():
    assert compare_versions("foo", "1.2.3") is None
    assert compare_versions("1.2.3", "bar") is None


@pytest.mark.asyncio
async def test_update_service_uses_cache_until_forced():
    provider = _FakeProvider()
    service = UpdateService(provider=provider, current_version="1.2.0", cache_ttl_seconds=999)

    first = await service.check_for_update(include_prerelease=False, force=False)
    second = await service.check_for_update(include_prerelease=False, force=False)
    third = await service.check_for_update(include_prerelease=False, force=True)

    assert first["ok"] is True
    assert first["update_available"] is True
    assert second["cached"] is True
    assert third["cached"] is False
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_update_service_prerelease_channel():
    provider = _FakeProvider()
    service = UpdateService(provider=provider, current_version="1.2.0", cache_ttl_seconds=999)

    stable = await service.check_for_update(include_prerelease=False)
    pre = await service.check_for_update(include_prerelease=True)

    assert stable["channel"] == "stable"
    assert pre["channel"] == "prerelease"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_server_dispatch_routes_update_messages():
    server = HushClawServer.__new__(HushClawServer)
    server._handle_check_update = AsyncMock()
    server._handle_run_update = AsyncMock()
    server._handle_save_update_policy = AsyncMock()

    class _WS:
        async def send(self, _msg):
            return None

    ws = _WS()
    session_ids = {}

    await server._dispatch(ws, {"type": "check_update"}, session_ids)
    await server._dispatch(ws, {"type": "run_update"}, session_ids)
    await server._dispatch(ws, {"type": "save_update_policy", "config": {}}, session_ids)

    server._handle_check_update.assert_awaited_once()
    server._handle_run_update.assert_awaited_once()
    server._handle_save_update_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_session_status_tracks_running_sessions():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(msg)

    ws = _WS()
    await server._emit_session_status(ws, "s-1", "running", "start")
    assert "s-1" in server._running_sessions
    await server._emit_session_status(ws, "s-1", "idle", "done")
    assert "s-1" not in server._running_sessions


@pytest.mark.asyncio
async def test_run_update_blocked_when_sessions_running():
    class _UpdateCfg:
        upgrade_timeout_seconds = 900

    class _BaseCfg:
        update = _UpdateCfg()

    class _Agent:
        config = _BaseCfg()

    class _Gateway:
        base_agent = _Agent()

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(msg)

    server = HushClawServer.__new__(HushClawServer)
    server._gateway = _Gateway()
    server._running_sessions = {"s-a"}
    server._upgrade_lock = asyncio.Lock()
    server._upgrade_in_progress = False
    server._connected_clients = set()
    server._update_executor = AsyncMock()

    ws = _WS()
    await server._handle_run_update(ws, {"type": "run_update"},)

    assert ws.items, "Expected update_result to be sent"
    payload = ws.items[-1]
    assert "update_result" in payload
    assert "active sessions" in payload
    server._update_executor.run_update.assert_not_called()
