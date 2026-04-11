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
from hushclaw.tools.builtins.memory_tools import recall
from hushclaw.providers.openai_raw import (
    _normalize_messages_for_gemini_openai_proxy,
    _sanitize_openai_messages_for_chat,
)
from hushclaw.providers.transsion import _normalize_router_base


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


def test_executor_drops_unexpected_kwargs():
    @tool(name="sum2", description="Add two numbers")
    def sum2(a: int, b: int) -> ToolResult:
        return ToolResult.ok(str(a + b))

    reg = ToolRegistry()
    reg.register(sum2)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("sum2", {"a": 1, "b": 2, "extra": "x"}))
    assert not result.is_error
    assert result.content == "3"


def test_executor_query_aliases_to_query():
    @tool(name="echo_query", description="Echo query string")
    def echo_query(query: str) -> ToolResult:
        return ToolResult.ok(query)

    reg = ToolRegistry()
    reg.register(echo_query)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("echo_query", {"keywords": ["alpha", "beta"]}))
    assert not result.is_error
    assert result.content == "alpha beta"


def test_executor_skill_name_aliases_to_query():
    @tool(name="echo_query_skill_alias", description="Echo query from skill_name alias")
    def echo_query_skill_alias(query: str) -> ToolResult:
        return ToolResult.ok(query)

    reg = ToolRegistry()
    reg.register(echo_query_skill_alias)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("echo_query_skill_alias", {"skill_name": "tiktok-insight"}))
    assert not result.is_error
    assert result.content == "tiktok-insight"


def test_recall_accepts_queries_alias():
    class _Mem:
        def recall(self, query: str, limit: int = 5) -> str:
            return f"Q={query}|L={limit}"

    r = recall(query="", queries=["alpha", "beta"], limit=3, _memory_store=_Mem())
    assert not r.is_error
    assert "Q=alpha beta|L=3" in r.content


def test_recall_accepts_keywords_alias():
    class _Mem:
        def recall(self, query: str, limit: int = 5) -> str:
            return f"Q={query}|L={limit}"

    r = recall(query="", keywords=["memory", "search"], limit=2, _memory_store=_Mem())
    assert not r.is_error
    assert "Q=memory search|L=2" in r.content


def test_transsion_gemini_role_normalization():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "tool output"},
        {"role": "user", "content": ""},
    ]
    _normalize_messages_for_gemini_openai_proxy(
        msgs,
        model="google/gemini-2.5-flash-lite",
        label="transsion",
    )
    roles = [m.get("role") for m in msgs]
    assert all(r in ("user", "model") for r in roles)
    assert "system" not in roles
    assert msgs[0]["role"] == "user"
    assert "[system]" in msgs[0]["content"]


def test_openai_raw_gpt5_uses_max_completion_tokens(monkeypatch):
    from hushclaw.providers.openai_raw import _sync_request

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self): return b'{"choices":[{"message":{"content":"ok"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'

    def _fake_urlopen(req, timeout=None, context=None):
        import json as _json
        captured["payload"] = _json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    out = _sync_request(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="azure/gpt-5.4",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        max_tokens=123,
        timeout=5,
        label="transsion",
    )
    assert "choices" in out
    assert "max_completion_tokens" in captured["payload"]
    assert "max_tokens" not in captured["payload"]


def test_sanitize_openai_messages_filters_empty_tool_name():
    msgs = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "x1", "type": "function", "function": {"name": "", "arguments": ""}},
            {"id": "x2", "type": "function", "function": {"name": "remember", "arguments": ""}},
        ],
    }]
    _sanitize_openai_messages_for_chat(msgs)
    tool_calls = msgs[0].get("tool_calls", [])
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "remember"
    assert tool_calls[0]["function"]["arguments"] == "{}"


def test_transsion_normalize_legacy_router_base():
    legacy = "https://airouter.aibotplatform.com/v1"
    assert _normalize_router_base(legacy) == "https://bus-ie.aibotplatform.com/v1"
    assert _normalize_router_base("") == "https://bus-ie.aibotplatform.com/v1"


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


def test_remember_skill_writes_file(tmp_path):
    from hushclaw.tools.builtins.memory_tools import remember_skill
    from types import SimpleNamespace

    cfg = SimpleNamespace(tools=SimpleNamespace(user_skill_dir=tmp_path, skill_dir=None))
    out = remember_skill(
        name="tiktok-insight",
        content="Playbook for TikTok insight mining",
        description="TikTok research workflow",
        _config=cfg,
        _skill_registry=None,
    )
    assert not out.is_error
    skill_file = tmp_path / "tiktok-insight" / "SKILL.md"
    assert skill_file.exists()
    text = skill_file.read_text()
    assert "tiktok-insight" in text
    assert "Playbook for TikTok" in text


# ── ToolResult API regression tests for all skill packages ──────────────────
# Prevents recurrence of: ToolResult.__init__() got an unexpected keyword argument 'output'
# All @tool-decorated functions must return ToolResult via .ok() or .error(), never the old
# constructor (ToolResult(output=...) / ToolResult(error=...)).

def _collect_skill_tool_files() -> list[Path]:
    """Return all tools/*.py files from skill-packages/."""
    skill_packages_root = Path(__file__).parent.parent / "skill-packages"
    return list(skill_packages_root.glob("*/tools/*.py"))


def test_skill_tool_files_exist():
    """Sanity check: skill package tool files must be present."""
    files = _collect_skill_tool_files()
    assert len(files) > 0, "No skill tool files found — check skill-packages/ layout"


def test_skill_tools_no_deprecated_toolresult_constructor():
    """Detect old-style ToolResult(output=...) or ToolResult(error=...) constructor calls.

    These cause TypeError at runtime. All skill tools must use ToolResult.ok(...) /
    ToolResult.error(...) instead.
    """
    import re
    # Matches: ToolResult(output=, ToolResult(error= (old constructor pattern)
    bad_pattern = re.compile(r'ToolResult\s*\(\s*(output|error)\s*=')
    violations = []
    for path in _collect_skill_tool_files():
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(source.splitlines(), start=1):
            if bad_pattern.search(line):
                violations.append(f"{path.relative_to(Path(__file__).parent.parent)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Deprecated ToolResult constructor usage found (use .ok() / .error() instead):\n"
        + "\n".join(violations)
    )


def test_skill_tools_load_via_registry():
    """Verify each skill tool file loads without import errors through ToolRegistry."""
    import importlib.util

    errors = []
    for path in _collect_skill_tool_files():
        try:
            spec = importlib.util.spec_from_file_location(f"_skill_test_{path.stem}", str(path))
            mod = importlib.util.module_from_spec(spec)
            # Attempt to exec the module; skip on missing optional deps (ImportError)
            try:
                spec.loader.exec_module(mod)
            except ImportError:
                pass  # Optional dependency not installed — not a ToolResult API issue
            except Exception as e:
                errors.append(f"{path.name}: {e}")
        except Exception as e:
            errors.append(f"{path.name}: {e}")
    assert not errors, "Skill tool files failed to load:\n" + "\n".join(errors)
