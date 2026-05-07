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
from hushclaw.app_connectors.registry import AppConnectorRegistry
from hushclaw.config.schema import AppConnectorsConfig, GitHubAppConnectorConfig
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


def test_app_connector_registry_only_exposes_configured_enabled_tools():
    class Secrets:
        def __init__(self, values):
            self.values = values

        def get(self, key, default=""):
            return self.values.get(key, default)

    disabled_cfg = AppConnectorsConfig(
        github=GitHubAppConnectorConfig(enabled=False, token_ref="gh.token"),
    )
    assert AppConnectorRegistry(disabled_cfg, Secrets({"gh.token": "x"})).enabled_tools() == []

    missing_secret_cfg = AppConnectorsConfig(
        github=GitHubAppConnectorConfig(enabled=True, token_ref="gh.token"),
    )
    assert AppConnectorRegistry(missing_secret_cfg, Secrets({})).enabled_tools() == []

    enabled_tools = AppConnectorRegistry(
        missing_secret_cfg,
        Secrets({"gh.token": "x"}),
    ).enabled_tools()
    assert {td.name for td in enabled_tools} == {"github_search", "github_read"}


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


def test_use_skill_does_not_write_skill_usage_to_memory():
    from hushclaw.tools.builtins.skill_tools import use_skill

    class _Registry:
        def get(self, name: str):
            if name == "deep-research":
                return {
                    "name": "deep-research",
                    "content": "Investigate carefully.",
                    "available": True,
                }
            return None

    class _Mem:
        def __init__(self):
            self.calls = 0

        def remember(self, *args, **kwargs):
            self.calls += 1

    mem = _Mem()
    out = use_skill(
        "deep-research",
        _skill_registry=_Registry(),
        _memory_store=mem,
        _session_id="sess-1234",
    )
    assert not out.is_error
    assert "# Skill: deep-research" in out.content
    assert "Runtime Output Contract" in out.content
    assert 'write_file("name.ext", content)' in out.content
    assert "do not write to `/files/...`" in out.content
    assert mem.calls == 0


def test_use_skill_accepts_slash_prefixed_skill_name():
    from hushclaw.tools.builtins.skill_tools import use_skill

    class _Registry:
        def get(self, name: str):
            if name == "ppt-argument-factory":
                return {
                    "name": "ppt-argument-factory",
                    "content": "Build argument-first slides.",
                    "available": True,
                }
            return None

    out = use_skill("/ppt-argument-factory", _skill_registry=_Registry())
    assert not out.is_error
    assert "# Skill: ppt-argument-factory" in out.content


def test_remember_infers_memory_kind_from_note_type():
    from hushclaw.tools.builtins.memory_tools import remember

    class _Mem:
        def __init__(self):
            self.kwargs = None

        def remember(self, *args, **kwargs):
            self.kwargs = kwargs
            return "note-12345678"

    mem = _Mem()
    out = remember(
        content="The user prefers concise answers",
        note_type="preference",
        _memory_store=mem,
        _config=SimpleNamespace(agent=SimpleNamespace(memory_scope="")),
    )
    assert not out.is_error
    assert mem.kwargs["memory_kind"] == "user_model"
    assert "kind=user_model" in out.content


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
    # bus-ie is the auth/control-plane host; legacy configs may store it as the
    # provider base_url, but inference calls must go to airouter.
    assert _normalize_router_base("https://airouter.aibotplatform.com/v1") == "https://airouter.aibotplatform.com/v1"
    assert _normalize_router_base("https://bus-ie.aibotplatform.com/v1") == "https://airouter.aibotplatform.com/v1"
    assert _normalize_router_base("https://bus-ie.aibotplatform.com") == "https://airouter.aibotplatform.com/v1"
    # Empty string → default router base with /v1 appended
    assert _normalize_router_base("") == "https://airouter.aibotplatform.com/v1"
    # URL without path → /v1 added
    assert _normalize_router_base("https://airouter.aibotplatform.com") == "https://airouter.aibotplatform.com/v1"
    # Custom non-control-plane router endpoints are preserved.
    assert _normalize_router_base("https://router.example.com/custom/v1") == "https://router.example.com/custom/v1"


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


def test_read_file_falls_back_to_workspace_files_for_relative_paths(tmp_path):
    from hushclaw.tools.builtins.file_tools import read_file, write_file

    workspace_dir = tmp_path / "workspace"
    cfg = SimpleNamespace(agent=SimpleNamespace(workspace_dir=workspace_dir))

    wr = write_file("ai_token_logic_final.md", "# Token Logic", _config=cfg)
    assert not wr.is_error

    rd = read_file("ai_token_logic_final.md", _config=cfg)
    assert not rd.is_error
    assert "# Token Logic" in rd.content


def test_read_file_rejects_files_url_with_helpful_error(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import read_file, write_file

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        wr = write_file("ai_token_logic_final.md", "# Token Logic", _config=cfg, _memory_store=memory)
        assert not wr.is_error
        assert wr.artifact_id

        # /files/ URLs are WebUI serving URLs — read_file must reject them with a clear error
        rd = read_file(f"/files/{wr.artifact_id}", _config=cfg, _memory_store=memory)
        assert rd.is_error
        assert "/files/" in rd.content
        assert "WebUI" in rd.content or "filesystem path" in rd.content
    finally:
        memory.close()


def test_write_file_rejects_files_prefix(tmp_path):
    from hushclaw.tools.builtins.file_tools import write_file

    upload_dir = tmp_path / "uploads"
    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=upload_dir))
    res = write_file("/files/reports/q1.txt", "hello", _config=cfg)
    assert res.is_error
    assert "/files/' paths are read-only served URLs" in res.content
    assert not upload_dir.exists()


def test_write_file_appends_hushclaw_watermark_to_markdown(tmp_path):
    from hushclaw.tools.builtins.file_tools import write_file

    target = tmp_path / "report.md"
    res = write_file(str(target), "# Report\n\nBody copy")

    assert not res.is_error
    text = target.read_text(encoding="utf-8")
    assert text == "# Report\n\nBody copy\n\n---\n*Generated by hushclaw*\n"


def test_write_file_does_not_duplicate_hushclaw_markdown_watermark(tmp_path):
    from hushclaw.tools.builtins.file_tools import write_file

    target = tmp_path / "report.md"
    content = "# Report\n\nBody copy\n\n---\n*Generated by hushclaw*\n"
    res = write_file(str(target), content)

    assert not res.is_error
    text = target.read_text(encoding="utf-8")
    assert text == content


def test_write_file_skill_definition_is_not_registered_as_generated_file(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import write_file

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        skill_path = workspace_dir / "skills" / "demo-skill" / "SKILL.md"
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        res = write_file(
            str(skill_path),
            "---\nname: demo-skill\n---\n\n## Workflow\n- Demo\n",
            _config=cfg,
            _memory_store=memory,
        )

        assert not res.is_error
        assert "not added to Files" in res.content
        assert skill_path.exists()
        rows = memory.conn.execute("SELECT original_name, source FROM uploaded_files").fetchall()
        assert rows == []
    finally:
        memory.close()


def test_make_download_url_returns_structured_relative_url(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url

    src = tmp_path / "demo.txt"
    src.write_text("hello", encoding="utf-8")
    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads"))
    res = make_download_url(str(src), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["trusted"] is True
    assert payload["kind"] == "file"
    assert payload["artifact_id"]
    assert payload["url"].startswith("/files/artifacts/")
    assert payload["root_url"].startswith("/files/artifacts/")
    assert payload["file_id"] == payload["artifact_id"]
    assert (cfg.server.upload_dir / "artifacts" / payload["artifact_id"] / "demo.txt").exists()
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
    assert payload["url"].startswith("/files/artifacts/")
    assert payload["absolute_url"].startswith("https://app.example.com/files/artifacts/")
    assert payload["absolute_root_url"].startswith("https://app.example.com/files/artifacts/")


def test_make_download_url_directory_registers_artifact(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url

    site_dir = tmp_path / "site"
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text(
        '<link rel="stylesheet" href="./assets/base.css"><h1>Hello</h1>',
        encoding="utf-8",
    )
    (assets_dir / "base.css").write_text("body{color:red;}", encoding="utf-8")

    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads"))
    res = make_download_url(str(site_dir), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["kind"] == "directory"
    assert payload["url"].startswith("/files/artifacts/")
    assert payload["entry_url"].endswith("/index.html")
    assert payload["root_url"].endswith(f"/{payload['artifact_id']}/")
    assert (cfg.server.upload_dir / "artifacts" / payload["artifact_id"] / "index.html").exists()
    assert (cfg.server.upload_dir / "artifacts" / payload["artifact_id"] / "assets" / "base.css").exists()


def test_make_download_bundle_registers_directory(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_bundle

    site_dir = tmp_path / "deck"
    (site_dir / "assets").mkdir(parents=True)
    (site_dir / "index.html").write_text("<h1>Deck</h1>", encoding="utf-8")
    (site_dir / "assets" / "runtime.js").write_text("console.log('ok')", encoding="utf-8")

    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads"))
    res = make_download_bundle(str(site_dir), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["kind"] == "directory"
    assert payload["entry_url"].endswith("/index.html")
    assert (cfg.server.upload_dir / "artifacts" / payload["artifact_id"] / "assets" / "runtime.js").exists()


def test_make_download_url_html_file_stays_file_artifact(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url

    html = tmp_path / "report.html"
    html.write_text("<h1>Hello</h1>", encoding="utf-8")

    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads"))
    res = make_download_url(str(html), _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["kind"] == "file"
    assert payload["url"].endswith("/report.html")
    assert "entry_url" not in payload
    assert (cfg.server.upload_dir / "artifacts" / payload["artifact_id"] / "report.html").exists()


def test_output_dir_injected_into_skill_tool(tmp_path):
    """_output_dir is injected from executor context into tool functions that declare it."""
    import json
    from pathlib import Path
    from hushclaw.tools.executor import ToolExecutor
    from hushclaw.tools.registry import ToolRegistry
    from hushclaw.tools.base import tool, ToolResult

    registry = ToolRegistry()
    received: dict = {}

    @tool(name="test_capture_output_dir", description="capture _output_dir")
    def _capture(_output_dir: Path | None = None) -> ToolResult:
        received["output_dir"] = _output_dir
        return ToolResult.ok(json.dumps({"ok": True}))

    registry.register(_capture)

    out_dir = tmp_path / "uploads"
    out_dir.mkdir()
    executor = ToolExecutor(registry)
    executor.set_context(_output_dir=out_dir)

    import asyncio
    asyncio.run(executor.execute("test_capture_output_dir", {}))

    assert received["output_dir"] == out_dir


def test_output_dir_get_context_value(tmp_path):
    """get_context_value returns _output_dir that was set via set_context."""
    from pathlib import Path
    from hushclaw.tools.executor import ToolExecutor
    from hushclaw.tools.registry import ToolRegistry

    out_dir = tmp_path / "uploads"
    executor = ToolExecutor(ToolRegistry())
    executor.set_context(_output_dir=out_dir)

    assert executor.get_context_value("_output_dir") == out_dir
    assert executor.get_context_value("_missing_key") is None


def test_jina_read_uses_shared_ssl_context(monkeypatch):
    from hushclaw.tools.builtins.web_tools import jina_read

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self, _size=-1): return b"# Clean markdown"

    sentinel_context = object()

    def _fake_urlopen(req, timeout=None, context=None):
        captured["timeout"] = timeout
        captured["context"] = context
        return _Resp()

    monkeypatch.setattr("hushclaw.tools.builtins.web_tools.make_ssl_context", lambda: sentinel_context)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = jina_read("https://example.com/article")
    assert not result.is_error
    assert captured["timeout"] == 30
    assert captured["context"] is sentinel_context


def test_jina_read_normalizes_non_ascii_url(monkeypatch):
    from hushclaw.tools.builtins.web_tools import jina_read

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self, _size=-1): return b"# Clean markdown"

    def _fake_urlopen(req, timeout=None, context=None):
        captured["full_url"] = req.full_url
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = jina_read('https://www.google.com/search?q=%22Muse+Spark%22+评价+反馈')
    assert not result.is_error
    assert "%E8%AF%84%E4%BB%B7" in captured["full_url"]
    assert "评价" not in captured["full_url"]


def test_fetch_url_opener_uses_https_handler_with_ssl_context():
    from urllib.request import HTTPSHandler

    from hushclaw.tools.builtins.web_tools import _opener

    handlers = [h for h in _opener.handlers if isinstance(h, HTTPSHandler)]
    assert handlers, "Expected fetch_url opener to include an HTTPSHandler"
    https_handler = handlers[0]
    assert getattr(https_handler, "_context", None) is not None


def test_normalize_url_percent_encodes_non_ascii_query():
    from hushclaw.tools.builtins.web_tools import _normalize_url

    out = _normalize_url('https://www.google.com/search?q=%22Muse+Spark%22+评价+反馈')
    assert out.startswith("https://www.google.com/search?q=")
    assert "%E8%AF%84%E4%BB%B7" in out
    assert "评价" not in out


def test_skill_agent_tools_includes_update_agent():
    reg = ToolRegistry()
    skill_tools_dir = Path(__file__).parent.parent / "skill-packages" / "hushclaw-skill-agent-tools" / "tools"
    reg.load_plugins(skill_tools_dir)
    names = [t.name for t in reg.list_tools()]
    assert "update_agent" in names
    assert "run_hierarchical" in names


def test_remember_skill_writes_file(tmp_path):
    from hushclaw.tools.builtins.memory_tools import remember_skill
    from hushclaw.skills.manager import SkillManager
    from hushclaw.skills.installer import SkillInstaller
    from hushclaw.skills.validator import SkillValidator

    manager = SkillManager(
        registry=None,
        installer=SkillInstaller(),
        validator=SkillValidator(),
        install_dir=tmp_path,
    )
    out = remember_skill(
        name="tiktok-insight",
        content="Playbook for TikTok insight mining",
        description="TikTok research workflow",
        _skill_manager=manager,
    )
    assert not out.is_error
    skill_file = tmp_path / "tiktok-insight" / "SKILL.md"
    assert skill_file.exists()
    text = skill_file.read_text()
    assert "tiktok-insight" in text
    assert "Playbook for TikTok" in text


def test_evolve_skill_patch_appends_refinement(tmp_path):
    from hushclaw.tools.builtins.skill_evolution_tools import evolve_skill
    from hushclaw.skills.manager import SkillManager
    from hushclaw.skills.installer import SkillInstaller
    from hushclaw.skills.validator import SkillValidator
    from hushclaw.skills.loader import SkillRegistry

    (tmp_path / "demo-skill").mkdir()
    skill_file = tmp_path / "demo-skill" / "SKILL.md"
    skill_file.write_text(
        "---\nname: demo-skill\ndescription: Demo\nversion: \"1.0.0\"\n---\n\n## Workflow\n- Step one\n",
        encoding="utf-8",
    )
    registry = SkillRegistry([(tmp_path, "user")])
    manager = SkillManager(
        registry=registry,
        installer=SkillInstaller(),
        validator=SkillValidator(),
        install_dir=tmp_path,
    )
    out = evolve_skill(
        skill_name="demo-skill",
        mode="patch",
        observation="Add a verification step after fetching sources.",
        _skill_manager=manager,
    )
    assert not out.is_error
    text = skill_file.read_text(encoding="utf-8")
    assert "## Refinements" in text
    assert "verification step" in text


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
