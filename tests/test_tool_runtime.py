"""Tests for the new centralized tool runtime boundary."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.runtime.policy import PolicyGate
from hushclaw.runtime.tool_runtime import ToolCall, ToolRuntime
from hushclaw.tools.base import ToolResult, tool
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.registry import ToolRegistry
from hushclaw.tools.runtime_context import ToolRuntimeContext


def test_executor_injects_runtime_context():
    received = {}

    @tool(name="capture_runtime", description="Capture typed runtime context")
    def capture_runtime(_runtime=None) -> ToolResult:
        received["runtime"] = _runtime
        return ToolResult.ok("ok")

    reg = ToolRegistry()
    reg.register(capture_runtime)
    executor = ToolExecutor(reg, timeout=5)
    runtime_context = ToolRuntimeContext(session_id="sess-1")
    executor.set_runtime_context(runtime_context)
    result = asyncio.run(executor.execute("capture_runtime", {}))
    assert not result.is_error
    assert received["runtime"] is runtime_context


def test_tool_runtime_blocks_dangerous_run_shell_command():
    @tool(name="run_shell", description="Fake shell tool", mutating=True)
    async def fake_run_shell(command: str) -> ToolResult:
        return ToolResult.ok(f"executed {command}")

    reg = ToolRegistry()
    reg.register(fake_run_shell)
    executor = ToolExecutor(reg, timeout=5)
    runtime = ToolRuntime(
        executor=executor,
        policy_gate=PolicyGate(),
        runtime_context=ToolRuntimeContext(session_id="sess-2"),
    )
    record = asyncio.run(
        runtime.execute(ToolCall(name="run_shell", arguments={"command": "rm -rf /tmp/demo && rm -rf /"}))
    )
    assert record.result.is_error
    assert "Blocked by runtime policy" in record.result.content


def test_tool_runtime_can_cancel_run_shell_via_confirm_fn():
    @tool(name="run_shell", description="Fake shell tool", mutating=True)
    async def fake_run_shell(command: str) -> ToolResult:
        return ToolResult.ok(f"executed {command}")

    reg = ToolRegistry()
    reg.register(fake_run_shell)
    executor = ToolExecutor(reg, timeout=5)
    runtime_context = ToolRuntimeContext(session_id="sess-3")
    runtime_context.set_extra("_confirm_fn", lambda command: False)
    runtime = ToolRuntime(
        executor=executor,
        policy_gate=PolicyGate(),
        runtime_context=runtime_context,
    )
    record = asyncio.run(runtime.execute(ToolCall(name="run_shell", arguments={"command": "echo hi"})))
    assert record.result.is_error
    assert record.result.content == "Cancelled by user."
