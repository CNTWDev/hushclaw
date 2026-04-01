"""Tests for the tool system."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.tools.base import tool, ToolResult, _build_schema
from hushclaw.tools.registry import ToolRegistry
from hushclaw.tools.executor import ToolExecutor


def test_tool_decorator():
    @tool(name="test_greet", description="Say hello")
    def greet(name: str, times: int = 1) -> ToolResult:
        return ToolResult.ok(f"Hello {name}! " * times)

    assert hasattr(greet, "_hushclaw_tool")
    td = greet._hushclaw_tool
    assert td.name == "test_greet"
    assert "name" in td.parameters["properties"]
    assert "name" in td.parameters["required"]
    assert "times" not in td.parameters["required"]


def test_schema_generation():
    def fn(query: str, limit: int = 5) -> ToolResult:
        pass

    schema = _build_schema(fn)
    assert schema["properties"]["query"]["type"] == "string"
    assert schema["properties"]["limit"]["type"] == "integer"
    assert "query" in schema["required"]
    assert "limit" not in schema["required"]


def test_tool_result():
    ok = ToolResult.ok("success")
    assert not ok.is_error
    assert ok.content == "success"

    err = ToolResult.error("oops")
    assert err.is_error
    assert err.content == "oops"


def test_registry_register():
    reg = ToolRegistry()
    reg.load_builtins()
    tools = reg.list_tools()
    names = [t.name for t in tools]
    assert "remember" in names
    assert "recall" in names
    assert "get_time" in names


def test_registry_enabled_filter():
    reg = ToolRegistry()
    reg.load_builtins(enabled=["get_time", "platform_info"])
    names = [t.name for t in reg.list_tools()]
    assert "get_time" in names
    assert "remember" not in names


def test_executor_sync_tool():
    @tool(name="add_nums", description="Add two numbers")
    def add(a: int, b: int) -> ToolResult:
        return ToolResult.ok(str(a + b))

    reg = ToolRegistry()
    reg.register(add)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("add_nums", {"a": 3, "b": 4}))
    assert result.content == "7"
    assert not result.is_error


def test_executor_async_tool():
    @tool(name="async_echo", description="Echo async")
    async def async_echo(msg: str) -> ToolResult:
        return ToolResult.ok(f"echo: {msg}")

    reg = ToolRegistry()
    reg.register(async_echo)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("async_echo", {"msg": "hello"}))
    assert result.content == "echo: hello"


def test_executor_unknown_tool():
    reg = ToolRegistry()
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("nonexistent", {}))
    assert result.is_error
    assert "Unknown tool" in result.content


def test_builtin_system_tools():
    from hushclaw.tools.builtins.system_tools import get_time, platform_info
    r1 = get_time()
    assert not r1.is_error
    assert "T" in r1.content  # ISO format

    r2 = platform_info()
    assert not r2.is_error
    assert "python" in r2.content.lower()


def test_builtin_file_tools(tmp_path):
    from hushclaw.tools.builtins.file_tools import read_file, write_file, list_dir
    test_file = tmp_path / "test.txt"

    wr = write_file(str(test_file), "hello hushclaw")
    assert not wr.is_error

    rd = read_file(str(test_file))
    assert not rd.is_error
    assert "hello hushclaw" in rd.content

    ld = list_dir(str(tmp_path))
    assert not ld.is_error
    assert "test.txt" in ld.content

    rd_missing = read_file(str(tmp_path / "missing.txt"))
    assert rd_missing.is_error


def test_write_file_files_prefix_returns_download_url(tmp_path):
    from hushclaw.tools.builtins.file_tools import write_file

    upload_dir = tmp_path / "uploads"
    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=upload_dir))
    res = write_file("/files/reports/q1.txt", "hello", _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["trusted"] is True
    assert payload["url"] == "/files/q1.txt"
    assert payload["name"] == "q1.txt"
    assert payload["file_id"] == ""
    assert payload["download"]["trusted"] is True
    assert payload["download"]["url"] == "/files/q1.txt"
    assert (upload_dir / "q1.txt").exists()


def test_make_download_url_returns_structured_relative_url(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url

    src = tmp_path / "demo.txt"
    src.write_text("hello", encoding="utf-8")
    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads"))
    res = make_download_url(str(src), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["trusted"] is True
    assert payload["url"].startswith("/files/")
    assert "absolute_url" not in payload


def test_make_download_url_includes_absolute_when_public_base_set(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url

    src = tmp_path / "report.md"
    src.write_text("# report", encoding="utf-8")
    cfg = SimpleNamespace(
        server=SimpleNamespace(
            upload_dir=tmp_path / "uploads",
            public_base_url="https://app.example.com",
        )
    )
    res = make_download_url(str(src), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["url"].startswith("/files/")
    assert payload["absolute_url"].startswith("https://app.example.com/files/")


def test_skill_agent_tools_includes_update_agent():
    reg = ToolRegistry()
    skill_tools_dir = Path(__file__).parent.parent / "skill-packages" / "hushclaw-skill-agent-tools" / "tools"
    reg.load_plugins(skill_tools_dir)
    names = [t.name for t in reg.list_tools()]
    assert "update_agent" in names
    assert "run_hierarchical" in names
