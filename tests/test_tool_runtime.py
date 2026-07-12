"""Tests for the new centralized tool runtime boundary."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.config.schema import Config
from hushclaw.runtime.policy import PolicyGate
from hushclaw.runtime.tool_runtime import ToolCall, ToolRuntime
from hushclaw.tools.base import ToolResult, tool
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.registry import ToolRegistry
from hushclaw.tools.runtime_context import ToolRuntimeContext
from hushclaw.memory.store import MemoryStore


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


def test_executor_offloads_large_tool_output_to_artifact_store():
    @tool(name="large_output", description="Return large output")
    def large_output() -> ToolResult:
        return ToolResult.ok("A" * 13_500)

    with tempfile.TemporaryDirectory() as d:
        memory = MemoryStore(Path(d), embed_provider="local")
        reg = ToolRegistry()
        reg.register(large_output)
        executor = ToolExecutor(reg, timeout=5)
        executor.set_runtime_context(ToolRuntimeContext(session_id="sess-large", memory=memory))

        result = asyncio.run(executor.execute("large_output", {}))

        assert not result.is_error
        assert result.artifact_id
        assert result.metadata["budgeted"] is True
        assert result.metadata["artifact_offloaded"] is True
        assert result.metadata["instruction_boundary"] == "untrusted_context"
        assert result.metadata["original_chars"] == 13_500
        assert len(result.content) < 2_000
        assert "Tool output budget" in result.content
        assert "13,500 chars" in result.content
        assert f'read_artifact(artifact_id="{result.artifact_id}")' in result.content
        stored = memory.artifacts.load(result.artifact_id)
        assert stored == b"A" * 13_500


def test_read_artifact_reads_budgeted_tool_output():
    from hushclaw.tools.builtins.artifact_tools import read_artifact

    with tempfile.TemporaryDirectory() as d:
        memory = MemoryStore(Path(d), embed_provider="local")
        artifact_id = memory.artifacts.save("sess-art", "hello artifact", tool_name="demo")

        result = read_artifact(artifact_id, _memory_store=memory)

        assert not result.is_error
        assert result.content == "hello artifact"
        assert result.metadata["artifact_id"] == artifact_id
        assert result.metadata["tool_name"] == "demo"


def test_read_artifact_reports_missing_artifact():
    from hushclaw.tools.builtins.artifact_tools import read_artifact

    with tempfile.TemporaryDirectory() as d:
        memory = MemoryStore(Path(d), embed_provider="local")

        result = read_artifact("art-missing", _memory_store=memory)

        assert result.is_error
        assert "Artifact not found" in result.content


def test_executor_applies_preview_without_artifact_store_when_memory_missing():
    @tool(name="large_no_store", description="Return large output without store")
    def large_no_store() -> ToolResult:
        return ToolResult.ok("B" * 13_500)

    reg = ToolRegistry()
    reg.register(large_no_store)
    executor = ToolExecutor(reg, timeout=5)

    result = asyncio.run(executor.execute("large_no_store", {}))

    assert not result.is_error
    assert not result.artifact_id
    assert result.metadata["budgeted"] is True
    assert result.metadata["artifact_offloaded"] is False
    assert len(result.content) < 2_000
    assert "no artifact store is available" in result.content


def test_tool_runtime_records_file_mutation_summary(tmp_path):
    @tool(name="write_file", description="Fake write", mutating=True)
    def fake_write_file(path: str, content: str) -> ToolResult:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return ToolResult.ok("written")

    cfg = Config()
    cfg.agent.workspace_dir = tmp_path
    reg = ToolRegistry()
    reg.register(fake_write_file)
    runtime = ToolRuntime(
        executor=ToolExecutor(reg, timeout=5),
        policy_gate=PolicyGate(),
        runtime_context=ToolRuntimeContext(session_id="sess-file", config=cfg, registry=reg),
    )

    record = asyncio.run(
        runtime.execute(ToolCall(name="write_file", arguments={"path": "ok.py", "content": "x = 1\n"}))
    )

    summary = record.result.metadata["mutation_summary"]
    assert summary["files"][0]["changed"] is True
    assert summary["diagnostics"][0]["checker"] == "python-ast"
    assert summary["diagnostics"][0]["ok"] is True


def test_tool_runtime_marks_missing_file_as_failed_verification(tmp_path):
    @tool(name="write_file", description="Fake write", mutating=True)
    def fake_write_file(path: str, content: str) -> ToolResult:
        return ToolResult.ok("claimed written")

    cfg = Config()
    cfg.agent.workspace_dir = tmp_path
    reg = ToolRegistry()
    reg.register(fake_write_file)
    runtime = ToolRuntime(
        executor=ToolExecutor(reg, timeout=5),
        policy_gate=PolicyGate(),
        runtime_context=ToolRuntimeContext(session_id="sess-file", config=cfg, registry=reg),
    )

    record = asyncio.run(
        runtime.execute(ToolCall(name="write_file", arguments={"path": "missing.txt", "content": "x"}))
    )

    assert record.result.is_error is True
    assert "Verification failed" in record.result.content
