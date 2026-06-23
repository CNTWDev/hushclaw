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
from hushclaw.server.session import _SessionEntry
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


@pytest.mark.asyncio
async def test_emit_session_runtime_includes_run_metadata():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}
    entry = _SessionEntry(session_id="s-run")
    entry.bind_thread("th-1", agent_name="default")
    run_id = entry.begin_run({"text": "hello"}, run_id="run-1", trigger_type="user")
    entry.queue_amendment({"text": "fix", "agent": "default"})
    entry.set_step(step_type="model", step_id="model:run-1:0", state="running", summary="Thinking")
    server._session_tasks = {"s-run": entry}

    class _WS:
        async def send(self, _msg):
            return None

    ws = _WS()
    await server._emit_session_runtime(ws, "s-run", status="running", reason="start")

    runtime = server._session_runtime["s-run"]
    assert runtime["run_id"] == run_id
    assert runtime["thread_id"] == "th-1"
    assert runtime["pending_amendments"] == 1
    assert runtime["active_step"]["step_type"] == "model"


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


def test_new_session_events_optimistically_surface_in_sidebar():
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    sessions_js = (_ROOT / "hushclaw" / "web" / "modules" / "panels" / "sessions.js").read_text(encoding="utf-8")
    panels_js = (_ROOT / "hushclaw" / "web" / "modules" / "panels.js").read_text(encoding="utf-8")

    assert 'ensureSessionRowVisible(data.session_id, { status: "running", summary: "Thinking" });' in websocket_js
    assert "scheduleSessionListRefresh(data.session_id);" in websocket_js
    assert "export function ensureSessionRowVisible(sessionId, runtime = null) {" in sessions_js
    assert "list.prepend(row);" in sessions_js
    assert "ensureSessionRowVisible," in panels_js


def test_session_switches_do_not_restore_old_scroll_positions():
    sessions_js = (_ROOT / "hushclaw" / "web" / "modules" / "panels" / "sessions.js").read_text(encoding="utf-8")
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert "saveScrollPosition(" not in sessions_js
    assert "requestSessionHistoryBottom(" not in sessions_js
    assert "noteSessionSwitchRequested(session_id);" in sessions_js
    assert "noteSessionHistoryReceived(" in websocket_js
    assert 'send({ type: "get_session_history", session_id });' in sessions_js


def test_websocket_handles_runtime_amendment_events():
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    state_js = (_ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")

    assert 'case "user_amendment_queued":' in websocket_js
    assert "Queued update" in websocket_js
    assert 'case "user_amendment_applied":' in websocket_js
    assert "Applying update" in websocket_js
    assert 'case "user_amendment_queue_limited":' in websocket_js
    assert "Merged extra updates" in websocket_js
    assert "safe_point" in websocket_js
    assert "setRuntimeTrace" not in websocket_js
    assert "clearRuntimeTrace();" not in websocket_js
    assert 'case "run_state_changed":' in websocket_js
    assert 'debugUiLifecycle("run_state_changed"' in websocket_js
    assert 'case "thread_state_changed":' in websocket_js
    assert 'case "step_state_changed":' in websocket_js
    assert "pushSessionRuntimeEvent" in websocket_js
    assert "sessionRuntimeLog" in state_js
    assert "sessionRuntimeToggle" in state_js


def test_session_entry_tracks_run_and_amendment_metadata():
    entry = _SessionEntry(session_id="s-meta")

    entry.bind_thread("th-1", agent_name="default")
    run_id = entry.begin_run({"text": "hello"}, run_id="run-1", trigger_type="user")
    entry.set_step(step_type="model", step_id="model:run-1:0", state="running", summary="Thinking", meta={"round": 0})
    queued = entry.queue_amendment({"text": "fix this", "agent": "default"})
    merged = entry.pop_merged_amendment()
    entry.complete_run(run_id, superseded=True)

    assert run_id == "run-1"
    assert queued["amendment_id"].startswith("amd-")
    assert merged["amendment_id"] == queued["amendment_id"]
    assert merged["queued_count"] == 1
    meta = entry.runtime_meta()
    assert meta["thread_id"] == "th-1"
    assert meta["active_step"]["step_type"] == ""
    assert meta["last_superseded_run_id"] == run_id
    assert meta["last_amendment_id"] == queued["amendment_id"]


def test_session_entry_limits_pending_amendments_by_merging_latest():
    entry = _SessionEntry(session_id="s-cap")

    queued = []
    for idx in range(7):
        queued.append(entry.queue_amendment({
            "text": f"update {idx}",
            "agent": "default",
            "client_turn_id": f"ct-{idx}",
        }))

    assert len(entry.pending_amendments) == 5
    tail = entry.pending_amendments[-1]
    assert tail["queue_limited"] is True
    assert tail["client_turn_id"] == "ct-6"
    assert "update 5" in tail["text"]
    assert "update 6" in tail["text"]

    merged = entry.pop_merged_amendment()
    assert merged is not None
    assert merged["queued_count"] == 5
    assert queued[-1]["amendment_id"] in merged["merged_amendment_ids"]
    assert queued[-2]["amendment_id"] in merged["merged_amendment_ids"]
    assert merged["client_turn_id"] == "ct-6"


def test_loop_checks_runtime_amendments_at_multiple_safe_points():
    loop_py = (_ROOT / "hushclaw" / "loop.py").read_text(encoding="utf-8")

    assert 'safe_point="before_model"' in loop_py
    assert 'safe_point="after_parallel_tools"' in loop_py
    assert 'safe_point=f"after_tool:{tc.name}"' in loop_py


def test_frontend_durable_queue_and_client_turn_stream_binding():
    state_js = (_ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    events_js = (_ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    chat_js = (_ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    websocket_js = (_ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert "state._durableSendQueue = []" not in state_js
    assert "_durableSendQueue: []" in state_js
    assert "function _queueDurableMessage(obj)" in state_js
    assert "export function flushPendingSendQueue()" in state_js
    assert 'return "queued";' in state_js
    assert "const clientTurnId = _makeClientTurnId();" in events_js
    assert "client_turn_id: clientTurnId" in events_js
    assert "insertUserMsg(displayText, referencePreviewItems, { clientTurnId, queued: sendResult === \"queued\" })" in events_js
    assert '_queuedTurnDispatches.add(clientTurnId);' in events_js
    assert "appendChunk(data.text, { clientTurnId: data.client_turn_id || \"\" })" in websocket_js
    assert "setChunkText(data.text, { clientTurnId: data.client_turn_id || \"\" })" in websocket_js
    assert "applyLiveMessageIds({" in websocket_js
    assert "const _userMsgElsByClientTurn = new Map();" in chat_js
    assert "const _aiMsgElsByClientTurn = new Map();" in chat_js
    assert "state._aiBubbleEl._streamingTextOnly = true;" in chat_js
    assert 'bodyEl.className = "streaming-markdown-body";' in chat_js


@pytest.mark.asyncio
async def test_handle_chat_keeps_waiting_user_runtime_after_done():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}
    server._pending_skill_prompts = {}

    async def _events(*_args, **_kwargs):
        yield {"type": "thread_run_bound", "thread_id": "th-wait", "run_id": "run-wait", "trigger_type": "user", "agent": "default"}
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
        yield {"type": "thread_run_bound", "thread_id": "th-route", "run_id": "run-route", "trigger_type": "user", "agent": "default"}
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
async def test_handle_chat_restarts_from_applied_runtime_amendment():
    server = HushClawServer.__new__(HushClawServer)
    server._running_sessions = set()
    server._session_runtime = {}
    server._pending_skill_prompts = {}

    class _Entry:
        def __init__(self):
            self.applied_amendment = {
                "text": "latest correction",
                "agent": "default",
                "images": [],
                "workspace": "",
                "client_now": "",
                "references": [],
                "amendment_id": "amd-1",
                "queued_count": 1,
            }
            self.active_run_id = ""
            self.runtime_thread = type("_Thread", (), {"thread_id": "th-amend", "agent_name": "default", "state": "active"})()
            self.runtime_run = type("_Run", (), {"state": "running", "trigger_type": "user", "active_step": type("_Step", (), {"step_id": "", "step_type": "", "state": "", "summary": "", "meta": {}})()})()

        def bind_thread(self, thread_id, *, agent_name=""):
            self.runtime_thread.thread_id = thread_id
            self.runtime_thread.agent_name = agent_name or self.runtime_thread.agent_name

        def begin_run(self, _payload, *, run_id="", trigger_type="user"):
            self.active_run_id = run_id or "run-1"
            self.runtime_run.state = "running"
            self.runtime_run.trigger_type = trigger_type
            return self.active_run_id

        def complete_run(self, run_id, *, superseded=False, state=""):
            if run_id == self.active_run_id:
                self.active_run_id = ""
                self.runtime_run.state = state or ("superseded" if superseded else "completed")

        def set_step(self, *, step_type, step_id, state, summary, meta=None):
            self.runtime_run.active_step = type("_Step", (), {
                "step_id": step_id,
                "step_type": step_type,
                "state": state,
                "summary": summary,
                "meta": dict(meta or {}),
            })()

        def clear_step(self, *, step_id=""):
            self.runtime_run.active_step = type("_Step", (), {"step_id": "", "step_type": "", "state": "", "summary": "", "meta": {}})()

        def runtime_meta(self):
            return {
                "thread_id": self.runtime_thread.thread_id,
                "thread_state": self.runtime_thread.state,
                "thread_agent": self.runtime_thread.agent_name,
                "run_id": self.active_run_id,
                "run_seq": 1,
                "run_state": self.runtime_run.state,
                "trigger_type": self.runtime_run.trigger_type,
                "pending_amendments": 0,
                "last_completed_run_id": "",
                "last_superseded_run_id": "",
                "last_amendment_id": "amd-1",
                "active_step": {
                    "step_id": self.runtime_run.active_step.step_id,
                    "step_type": self.runtime_run.active_step.step_type,
                    "state": self.runtime_run.active_step.state,
                    "summary": self.runtime_run.active_step.summary,
                    "meta": dict(self.runtime_run.active_step.meta or {}),
                },
            }

    calls = []

    async def _events(agent, text, session_id, **_kwargs):
        calls.append(text)
        yield {"type": "thread_run_bound", "thread_id": "th-amend", "run_id": "run-1", "trigger_type": "user", "agent": agent}
        if text == "hello":
            yield {"type": "user_amendment_applied", "text": "latest correction", "agent": agent, "safe_point": "before_model"}
            yield {"type": "done", "text": "", "stop_reason": "user_amendment", "input_tokens": 0, "output_tokens": 0}
            return
        yield {"type": "chunk", "text": "fixed answer"}
        yield {"type": "done", "text": "fixed answer", "stop_reason": "end_turn", "input_tokens": 1, "output_tokens": 1}

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
    server._session_tasks = {"s-amend": _Entry()}
    ws = _WS()

    await server._handle_chat(ws, {"text": "hello", "session_id": "s-amend"})

    assert calls == ["hello", "latest correction"]
    done = [item for item in ws.items if item.get("type") == "done"][-1]
    assert done["text"] == "fixed answer"
    runtimes = [item["runtime"] for item in ws.items if item.get("type") == "session_runtime"]
    assert any(runtime.get("run_id") == "run-1" for runtime in runtimes)
    assert any(runtime.get("thread_id") == "th-amend" for runtime in runtimes)
    run_states = [item for item in ws.items if item.get("type") == "run_state_changed"]
    assert any(item.get("state") == "started" for item in run_states)
    assert any(item.get("state") == "superseded" and item.get("reason") == "user_amendment" for item in run_states)
    assert any(item.get("state") == "completed" for item in run_states)
    thread_states = [item for item in ws.items if item.get("type") == "thread_state_changed"]
    assert any(item.get("thread_id") == "th-amend" for item in thread_states)
    step_states = [item for item in ws.items if item.get("type") == "step_state_changed"]
    assert any(item.get("step_type") == "model" for item in step_states)
    assert any(item.get("step_type") == "amendment" for item in step_states)


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
    assert payload["backup_required"] is False
    assert payload["backup_recommended"] is True
    assert "overwrite repo code" in payload["message"]
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
