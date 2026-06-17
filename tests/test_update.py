"""Tests for update check and upgrade orchestration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from hushclaw.server import HushClawServer
from hushclaw.server import update_handler
from hushclaw.update.executor import UpdateExecutor
from hushclaw.update.provider import ReleaseInfo
from hushclaw.update.service import UpdateService, compare_versions


_ROOT = Path(__file__).resolve().parents[1]


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
    server._handle_prepare_update = AsyncMock()
    server._handle_run_update = AsyncMock()
    server._handle_save_update_policy = AsyncMock()

    class _WS:
        async def send(self, _msg):
            return None

    ws = _WS()
    session_ids = {}

    await server._dispatch(ws, {"type": "check_update"}, session_ids)
    await server._dispatch(ws, {"type": "prepare_update"}, session_ids)
    await server._dispatch(ws, {"type": "run_update"}, session_ids)
    await server._dispatch(ws, {"type": "save_update_policy", "config": {}}, session_ids)

    server._handle_check_update.assert_awaited_once()
    server._handle_prepare_update.assert_awaited_once()
    server._handle_run_update.assert_awaited_once()
    server._handle_save_update_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_session_status_tracks_running_sessions():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(msg)

    ws = _WS()
    await server._emit_session_status(ws, "s-1", "running", "start")
    assert "s-1" in server._running_sessions
    assert server._session_runtime["s-1"]["status"] == "running"
    await server._emit_session_status(ws, "s-1", "idle", "done")
    assert "s-1" not in server._running_sessions
    assert server._session_runtime["s-1"]["status"] == "completed"
    assert any('"type": "session_runtime"' in item for item in ws.items)


@pytest.mark.asyncio
async def test_session_runtime_resets_started_at_for_new_turn():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}

    class _WS:
        async def send(self, _msg):
            return None

    ws = _WS()
    await server._emit_session_status(ws, "s-1", "running", "start")
    first_started = server._session_runtime["s-1"]["started_at"]
    await server._emit_session_status(ws, "s-1", "idle", "done")
    await server._emit_session_status(ws, "s-1", "running", "start")
    second_started = server._session_runtime["s-1"]["started_at"]

    assert second_started >= first_started
    assert second_started == server._session_runtime["s-1"]["updated_at"]


@pytest.mark.asyncio
async def test_emit_session_runtime_marks_waiting_user_not_running():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(msg)

    ws = _WS()
    await server._emit_session_runtime(
        ws,
        "s-wait",
        status="waiting_user",
        reason="awaiting_user",
        phase="waiting_user",
        summary="Waiting for sign-in",
        requires_user=True,
    )

    assert "s-wait" not in server._running_sessions
    runtime = server._session_runtime["s-wait"]
    assert runtime["status"] == "waiting_user"
    assert runtime["requires_user"] is True
    assert runtime["summary"] == "Waiting for sign-in"


def test_waiting_user_runtime_keeps_composer_available():
    state_js = (_ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert '["queued", "running"].includes(runtime?.status || getSessionStatus(sid))' in state_js
    assert 'const running = ["queued", "running"].includes(status);' in websocket_js
    assert 'const waitingUser = status === "waiting_user";' in websocket_js
    assert "if (running) {\n      rehydrateInProgressUi(sid);" in websocket_js


def test_websocket_startup_primes_only_the_active_tab_on_connect():
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    agents_js = (_ROOT / "hushclaw" / "web" / "modules" / "panels" / "agents.js").read_text(encoding="utf-8")

    assert 'switchTab(state.tab || "chat");' in websocket_js
    assert "function _loadTabData(tab)" in agents_js
    assert 'if (tab === "skills") {' in agents_js
    assert 'send({ type: "list_skills" });' in agents_js
    assert 'if (tab === "tasks") {' in agents_js
    assert "refreshTodos(0);" in agents_js
    assert 'if (tab === "insights") {' in agents_js


def test_session_switches_do_not_restore_old_scroll_positions():
    sessions_js = (_ROOT / "hushclaw" / "web" / "modules" / "panels" / "sessions.js").read_text(encoding="utf-8")
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert "saveScrollPosition(" not in sessions_js
    assert "requestSessionHistoryBottom(" not in sessions_js
    assert "noteSessionSwitchRequested(session_id);" in sessions_js
    assert "noteSessionHistoryReceived(" in websocket_js
    assert 'send({ type: "get_session_history", session_id });' in sessions_js


@pytest.mark.asyncio
async def test_handle_chat_keeps_waiting_user_runtime_after_done():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}
    server._pending_skill_prompts = {}

    async def _events(*_args, **_kwargs):
        yield {
            "type": "awaiting_user",
            "text": "Confirm?",
            "pending_tools": [],
            "stop_reason": "awaiting_user_confirmation",
        }
        yield {
            "type": "done",
            "text": "Confirm?",
            "stop_reason": "awaiting_user_confirmation",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    class _Gateway:
        def __init__(self):
            self.event_stream = _events
            self.base_agent = type(
                "_Agent",
                (),
                {"config": type("_Cfg", (), {"workspaces": type("_Workspaces", (), {"list": []})()})()},
            )()

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(msg)

    server._gateway = _Gateway()
    ws = _WS()

    await server._handle_chat(ws, {"text": "confirm first", "session_id": "s-wait"})

    assert server._session_runtime["s-wait"]["status"] == "waiting_user"
    assert "s-wait" not in server._running_sessions


@pytest.mark.asyncio
async def test_handle_chat_tags_stream_events_with_session_id():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}
    server._pending_skill_prompts = {}

    async def _events(*_args, **_kwargs):
        yield {"type": "chunk", "text": "hello"}
        yield {"type": "tool_call", "tool": "remember", "input": {}, "call_id": "tc-1"}
        yield {"type": "tool_result", "tool": "remember", "result": "ok", "call_id": "tc-1"}
        yield {"type": "done", "text": "hello", "input_tokens": 1, "output_tokens": 1}

    class _Gateway:
        def __init__(self):
            self.event_stream = _events
            self.base_agent = type(
                "_Agent",
                (),
                {"config": type("_Cfg", (), {"workspaces": type("_Workspaces", (), {"list": []})()})()},
            )()

    class _WS:
        def __init__(self):
            self.items = []

        async def send(self, msg):
            self.items.append(json.loads(msg))

    server._gateway = _Gateway()
    ws = _WS()

    await server._handle_chat(ws, {"text": "hello", "session_id": "s-route"})

    routed_types = {"chunk", "tool_call", "tool_result", "done"}
    routed = [item for item in ws.items if item.get("type") in routed_types]
    assert routed
    assert all(item.get("session_id") == "s-route" for item in routed)


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


@pytest.mark.asyncio
async def test_run_update_passes_overwrite_flags_to_executor():
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
    server._running_sessions = set()
    server._upgrade_lock = asyncio.Lock()
    server._upgrade_in_progress = False
    server._upgrade_state = {"in_progress": False}
    server._connected_clients = set()
    server._update_executor = AsyncMock()
    server._update_executor.launch_delegate = AsyncMock(return_value={
        "ok": False,
        "error": "stop here",
        "restart_required": False,
        "command": "bash install.sh --update",
    })

    ws = _WS()
    await server._handle_run_update(ws, {
        "type": "run_update",
        "overwrite_install": True,
        "backup_before_overwrite": True,
    })

    server._update_executor.launch_delegate.assert_awaited_once()
    _, kwargs = server._update_executor.launch_delegate.await_args
    assert kwargs["overwrite_install"] is True
    assert kwargs["backup_before_overwrite"] is True


@pytest.mark.asyncio
async def test_prepare_update_reports_dirty_install_state():
    class _UpdateCfg:
        channel = "stable"

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
            self.items.append(json.loads(msg))

    class _UpdateService:
        async def check_for_update(self, include_prerelease=False, force=False):
            return {
                "ok": True,
                "current_version": "0.5.2",
                "latest_version": "0.5.3",
                "release_url": "https://example.com/release",
                "published_at": "2026-06-17T00:00:00Z",
                "channel": "stable",
                "update_available": True,
            }

    ws = _WS()
    with patch.object(
        update_handler,
        "_detect_install_repo_state",
        return_value={
            "repo_path": "/tmp/hushclaw",
            "dirty_install": True,
            "dirty_files": [" M hushclaw/web/index.html"],
        },
    ):
        await update_handler.handle_prepare_update(ws, {}, _Gateway(), _UpdateService())

    assert ws.items
    payload = ws.items[-1]
    assert payload["type"] == "prepare_update_result"
    assert payload["dirty_install"] is True
    assert payload["backup_required"] is True
    assert payload["dirty_files"] == [" M hushclaw/web/index.html"]


def test_update_executor_pick_command_supports_overwrite_flags():
    executor = UpdateExecutor()
    cmd = executor._pick_command(
        overwrite_install=True,
        backup_before_overwrite=True,
    )
    assert "--update" in cmd
    assert "--overwrite-install" in cmd
    assert "--backup-before-overwrite" in cmd


def test_install_script_supports_repo_code_overwrite_install_policy():
    install_sh = (_ROOT / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (_ROOT / "install.ps1").read_text(encoding="utf-8")

    assert 'ORIGINAL_ARGS=("$@")' in install_sh
    assert "--overwrite-install" in install_sh
    assert "--backup-before-overwrite" in install_sh
    assert "repo_is_dirty()" in install_sh
    assert "backup_user_data()" in install_sh
    assert 'exec bash "$_REPO_INSTALLER" "${ORIGINAL_ARGS[@]}"' in install_sh
    assert "Proceeding to overwrite installation code. Runtime data lives outside the install repository." in install_sh
    assert "Use --backup-before-overwrite to save a pre-overwrite snapshot." in install_sh
    assert "-OverwriteInstall" in install_ps1
    assert "-BackupBeforeOverwrite" in install_ps1
    assert "Proceeding to overwrite installation code. Runtime data lives outside the install repository." in install_ps1
    assert "Use -BackupBeforeOverwrite to save a pre-overwrite snapshot." in install_ps1
