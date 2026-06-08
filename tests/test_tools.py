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
from hushclaw.tools.builtins.memory_tools import recall, search_notes
from hushclaw.app_connectors.registry import AppConnectorRegistry
from hushclaw.config.schema import (
    AppConnectorsConfig, GitHubAppConnectorConfig,
    RedditAppConnectorConfig, XAppConnectorConfig,
    GoogleWorkspaceAppConnectorConfig, NotionAppConnectorConfig, JiraAppConnectorConfig,
)
from hushclaw.providers.openai_raw import (
    _normalize_messages_for_gemini_openai_proxy,
    _sanitize_openai_messages_for_chat,
)
from hushclaw.providers.openai_transforms import parse_response_payload, parse_textual_tool_calls
from hushclaw.providers.transsion import _normalize_router_base


def assert_untrusted_tool_output(result: ToolResult, expected: str, tool_name: str) -> None:
    assert not result.is_error
    assert f"<untrusted_context source='tool:{tool_name}'" in result.content
    assert "-----BEGIN UNTRUSTED CONTENT-----" in result.content
    assert expected in result.content
    assert "-----END UNTRUSTED CONTENT-----" in result.content
    assert result.metadata.get("instruction_boundary") == "untrusted_context"


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
    assert "search_files" in names
    assert "web_search" in names


def test_memory_read_tools_are_marked_parallel_safe():
    assert recall._hushclaw_tool.parallel_safe
    assert search_notes._hushclaw_tool.parallel_safe


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

    social_cfg = AppConnectorsConfig(
        reddit=RedditAppConnectorConfig(enabled=True, access_token_ref="reddit.access"),
        x=XAppConnectorConfig(enabled=True, bearer_token_ref="x.bearer"),
    )
    social_tools = AppConnectorRegistry(
        social_cfg,
        Secrets({"reddit.access": "x", "x.bearer": "x"}),
    ).enabled_tools()
    assert {td.name for td in social_tools} == {
        "reddit_search", "reddit_read", "reddit_post", "reddit_comment",
        "x_search", "x_read_post", "x_post", "x_reply",
    }
    mutating = {td.name for td in social_tools if td.mutating}
    assert mutating == {"reddit_post", "reddit_comment", "x_post", "x_reply"}

    cfg = AppConnectorsConfig(
        github=GitHubAppConnectorConfig(enabled=True, token_ref="gh.token"),
        google_workspace=GoogleWorkspaceAppConnectorConfig(enabled=True, access_token_ref="gw.access"),
        notion=NotionAppConnectorConfig(enabled=True, token_ref="notion.token"),
        jira=JiraAppConnectorConfig(enabled=True, site_url="https://example.atlassian.net", token_ref="jira.token"),
        reddit=RedditAppConnectorConfig(enabled=True, access_token_ref="reddit.access"),
        x=XAppConnectorConfig(enabled=True, bearer_token_ref="x.bearer"),
    )
    status = AppConnectorRegistry(
        cfg,
        Secrets({
            "gh.token": "x",
            "gw.access": "x",
            "notion.token": "x",
            "jira.token": "x",
            "reddit.access": "x",
            "x.bearer": "x",
        }),
    ).status()
    assert status["google_workspace"]["configured"] is True
    assert status["notion"]["sdk"] == "notion-client"
    assert status["jira"]["auth"].startswith("Atlassian")
    assert status["reddit"]["configured"] is True
    assert status["x"]["sdk"] == "X API v2 via stdlib urllib"


def test_social_app_connector_write_tools_require_allow_actions():
    class Secrets:
        def __init__(self, values):
            self.values = values

        def get(self, key, default=""):
            return self.values.get(key, default)

    from hushclaw.app_connectors import reddit as reddit_mod
    from hushclaw.app_connectors import x as x_mod

    secrets = Secrets({"reddit.access": "token", "x.access": "token"})
    reddit_cfg = RedditAppConnectorConfig(enabled=True, access_token_ref="reddit.access", allow_actions=False)
    x_cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        allow_actions=False,
    )

    reddit_result = reddit_mod.post(reddit_cfg, secrets, subreddit="hushclaw", title="Blocked write")
    x_result = x_mod.post(x_cfg, secrets, text="Blocked write")

    assert reddit_result.is_error is True
    assert "write actions are disabled" in reddit_result.content
    assert x_result.is_error is True
    assert "write actions are disabled" in x_result.content


def test_x_post_publishes_after_chat_confirmation_gate(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def get(self, key, default=""):
            return "token"

    cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        allow_actions=True,
    )

    def fake_request(token, path, **kwargs):
        return 201, {"data": {"id": "tweet-1"}}

    monkeypatch.setattr(x_mod, "_request", fake_request)
    result = x_mod.post(cfg, Secrets(), text="Publish this")

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["action"] == "post"
    assert payload["result"]["data"]["id"] == "tweet-1"


def test_x_connection_uses_user_context_for_identity_check(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    calls = []

    class Secrets:
        def get(self, key, default=""):
            return {"x.access": "user-token"}.get(key, default)

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        return 200, {"data": {"username": "hushclaw"}}

    monkeypatch.setattr(x_mod, "_request", fake_request)
    cfg = XAppConnectorConfig(enabled=True, access_token_ref="x.access")

    result = x_mod.test_x_connection(cfg, Secrets())

    assert result["ok"] is True
    assert "user context" in result["message"]
    assert calls == [("user-token", "/users/me")]


def test_x_connection_does_not_use_users_me_with_bearer_token(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    calls = []

    class Secrets:
        def get(self, key, default=""):
            return {"x.bearer": "bearer-token"}.get(key, default)

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        return 200, {"data": []}

    monkeypatch.setattr(x_mod, "_request", fake_request)
    cfg = XAppConnectorConfig(enabled=True, bearer_token_ref="x.bearer")

    result = x_mod.test_x_connection(cfg, Secrets())

    assert result["ok"] is True
    assert "app-only read APIs" in result["message"]
    assert calls[0][0] == "bearer-token"
    assert calls[0][1].startswith("/tweets/search/recent?")
    assert "/users/me" not in calls[0][1]


def test_x_connection_refreshes_expired_user_token(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def __init__(self):
            self.values = {
                "x.access": "old-token",
                "x.refresh": "refresh-token",
                "x.client": "client-id",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    secrets = Secrets()
    calls = []

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        if token == "old-token":
            return 401, {"detail": "Unauthorized"}
        return 200, {"data": {"username": "hushclaw"}}

    def fake_refresh(config, secret_store):
        secret_store.set("x.access", "new-token")
        return "new-token"

    monkeypatch.setattr(x_mod, "_request", fake_request)
    monkeypatch.setattr(x_mod, "refresh_access_token", fake_refresh)
    cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        refresh_token_ref="x.refresh",
        oauth_client_id_ref="x.client",
    )

    result = x_mod.test_x_connection(cfg, secrets)

    assert result["ok"] is True
    assert calls == [("old-token", "/users/me"), ("new-token", "/users/me")]
    assert secrets.values["x.access"] == "new-token"


def test_x_refresh_access_token_persists_new_tokens(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def __init__(self):
            self.values = {
                "x.refresh": "refresh-token",
                "x.client": "client-id",
                "x.secret": "client-secret",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"access_token":"new-access","refresh_token":"new-refresh"}'

    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["data"] = req.data.decode("utf-8")
        captured["auth"] = req.headers.get("Authorization")
        return Resp()

    monkeypatch.setattr(x_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = XAppConnectorConfig(
        refresh_token_ref="x.refresh",
        access_token_ref="x.access",
        oauth_client_id_ref="x.client",
        oauth_client_secret_ref="x.secret",
    )
    secrets = Secrets()

    token = x_mod.refresh_access_token(cfg, secrets)

    assert token == "new-access"
    assert "grant_type=refresh_token" in captured["data"]
    assert "refresh_token=refresh-token" in captured["data"]
    assert captured["auth"].startswith("Basic ")
    assert secrets.values["x.access"] == "new-access"
    assert secrets.values["x.refresh"] == "new-refresh"


def test_x_refresh_access_token_requires_refresh_token():
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def get(self, key, default=""):
            return ""

    result = x_mod.refresh_access_token(XAppConnectorConfig(), Secrets())

    assert result.is_error is True
    assert "Reconnect X user OAuth" in result.content


def test_x_publish_post_retries_after_token_refresh(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def __init__(self):
            self.values = {
                "x.access": "old-token",
                "x.refresh": "refresh-token",
                "x.client": "client-id",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []

    def fake_request(token, path, **kwargs):
        calls.append((token, path, kwargs.get("method"), kwargs.get("data")))
        if token == "old-token":
            return 401, {"detail": "Unauthorized"}
        return 201, {"data": {"id": "tweet-1"}}

    def fake_refresh(config, secret_store):
        secret_store.set("x.access", "new-token")
        return "new-token"

    monkeypatch.setattr(x_mod, "_request", fake_request)
    monkeypatch.setattr(x_mod, "refresh_access_token", fake_refresh)
    cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        refresh_token_ref="x.refresh",
        oauth_client_id_ref="x.client",
        allow_actions=True,
    )
    secrets = Secrets()

    result = x_mod._publish_post(cfg, secrets, "Ship it")

    assert result.is_error is False
    assert calls == [
        ("old-token", "/tweets", "POST", {"text": "Ship it"}),
        ("new-token", "/tweets", "POST", {"text": "Ship it"}),
    ]
    assert secrets.values["x.access"] == "new-token"


def test_x_search_uses_bearer_without_refresh(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def get(self, key, default=""):
            return {"x.bearer": "bearer-token", "x.access": "user-token"}.get(key, default)

    calls = []

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        return 200, {"data": [{"id": "tweet-1", "text": "Hello"}]}

    def fail_refresh(config, secrets):
        raise AssertionError("bearer read path should not refresh OAuth tokens")

    monkeypatch.setattr(x_mod, "_request", fake_request)
    monkeypatch.setattr(x_mod, "refresh_access_token", fail_refresh)
    cfg = XAppConnectorConfig(enabled=True, bearer_token_ref="x.bearer", access_token_ref="x.access")

    result = x_mod.search(cfg, Secrets(), "python")

    assert result.is_error is False
    assert calls[0][0] == "bearer-token"
    assert calls[0][1].startswith("/tweets/search/recent?")


def test_x_search_retries_user_token_after_refresh(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def __init__(self):
            self.values = {
                "x.access": "old-token",
                "x.refresh": "refresh-token",
                "x.client": "client-id",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        if token == "old-token":
            return 401, {"detail": "Unauthorized"}
        return 200, {"data": [{"id": "tweet-1", "text": "Hello"}]}

    def fake_refresh(config, secret_store):
        secret_store.set("x.access", "new-token")
        return "new-token"

    monkeypatch.setattr(x_mod, "_request", fake_request)
    monkeypatch.setattr(x_mod, "refresh_access_token", fake_refresh)
    cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        refresh_token_ref="x.refresh",
        oauth_client_id_ref="x.client",
    )
    secrets = Secrets()

    result = x_mod.search(cfg, secrets, "python")

    assert result.is_error is False
    assert calls[0] == ("old-token", calls[0][1])
    assert calls[1] == ("new-token", calls[1][1])
    assert calls[0][1] == calls[1][1]
    assert calls[0][1].startswith("/tweets/search/recent?")
    assert secrets.values["x.access"] == "new-token"


def test_x_read_post_retries_user_token_after_refresh(monkeypatch):
    from hushclaw.app_connectors import x as x_mod

    class Secrets:
        def __init__(self):
            self.values = {
                "x.access": "old-token",
                "x.refresh": "refresh-token",
                "x.client": "client-id",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []

    def fake_request(token, path, **kwargs):
        calls.append((token, path))
        if token == "old-token":
            return 401, {"detail": "Unauthorized"}
        return 200, {"data": {"id": "tweet-1", "text": "Hello"}}

    def fake_refresh(config, secret_store):
        secret_store.set("x.access", "new-token")
        return "new-token"

    monkeypatch.setattr(x_mod, "_request", fake_request)
    monkeypatch.setattr(x_mod, "refresh_access_token", fake_refresh)
    cfg = XAppConnectorConfig(
        enabled=True,
        access_token_ref="x.access",
        refresh_token_ref="x.refresh",
        oauth_client_id_ref="x.client",
    )
    secrets = Secrets()

    result = x_mod.read_post(cfg, secrets, "tweet-1")

    assert result.is_error is False
    assert calls[0] == ("old-token", calls[0][1])
    assert calls[1] == ("new-token", calls[1][1])
    assert calls[0][1] == calls[1][1]
    assert calls[0][1].startswith("/tweets/tweet-1?")
    assert secrets.values["x.access"] == "new-token"


def test_x_filtered_stream_rules_are_hushclaw_tagged():
    from hushclaw.app_connectors.x_stream import normalize_stream_rules

    rules = normalize_stream_rules([
        {"tag": "brand", "value": "from:hushclaw"},
        {"tag": "brand", "value": "from:hushclaw"},
        "python lang:en",
        {"value": ""},
    ])
    assert rules == [
        {"tag": "hushclaw:brand", "value": "from:hushclaw"},
        {"tag": "hushclaw:rule-3", "value": "python lang:en"},
    ]


def test_executor_sync_tool():
    @tool(name="add_nums", description="Add two numbers")
    def add(a: int, b: int) -> ToolResult:
        return ToolResult.ok(str(a + b))

    reg = ToolRegistry()
    reg.register(add)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("add_nums", {"a": 3, "b": 4}))
    assert_untrusted_tool_output(result, "7", "add_nums")


def test_executor_async_tool():
    @tool(name="async_echo", description="Echo async")
    async def async_echo(msg: str) -> ToolResult:
        return ToolResult.ok(f"echo: {msg}")

    reg = ToolRegistry()
    reg.register(async_echo)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("async_echo", {"msg": "hello"}))
    assert_untrusted_tool_output(result, "echo: hello", "async_echo")


def test_executor_unknown_tool():
    reg = ToolRegistry()
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("nonexistent", {}))
    assert result.is_error
    assert "Unknown tool" in result.content


def test_executor_reports_missing_required_args_without_typeerror():
    @tool(name="needs_path_content")
    def needs_path_content(path: str, content: str) -> ToolResult:
        return ToolResult.ok("unused")

    reg = ToolRegistry()
    reg.register(needs_path_content)
    executor = ToolExecutor(reg, timeout=5)

    result = asyncio.run(executor.execute("needs_path_content", {}))

    assert result.is_error
    assert "missing required argument" in result.content
    assert "path" in result.content
    assert "content" in result.content


def test_executor_drops_unexpected_kwargs():
    @tool(name="sum2", description="Add two numbers")
    def sum2(a: int, b: int) -> ToolResult:
        return ToolResult.ok(str(a + b))

    reg = ToolRegistry()
    reg.register(sum2)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("sum2", {"a": 1, "b": 2, "extra": "x"}))
    assert_untrusted_tool_output(result, "3", "sum2")


def test_executor_query_aliases_to_query():
    @tool(name="echo_query", description="Echo query string")
    def echo_query(query: str) -> ToolResult:
        return ToolResult.ok(query)

    reg = ToolRegistry()
    reg.register(echo_query)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("echo_query", {"keywords": ["alpha", "beta"]}))
    assert_untrusted_tool_output(result, "alpha beta", "echo_query")


def test_executor_skill_name_aliases_to_query():
    @tool(name="echo_query_skill_alias", description="Echo query from skill_name alias")
    def echo_query_skill_alias(query: str) -> ToolResult:
        return ToolResult.ok(query)

    reg = ToolRegistry()
    reg.register(echo_query_skill_alias)
    executor = ToolExecutor(reg, timeout=5)
    result = asyncio.run(executor.execute("echo_query_skill_alias", {"skill_name": "tiktok-insight"}))
    assert_untrusted_tool_output(result, "tiktok-insight", "echo_query_skill_alias")


def test_recall_accepts_queries_alias():
    class _Mem:
        def recall(self, query: str, *, scopes=None, principal=None, limit: int = 5) -> str:
            return f"Q={query}|L={limit}"

    r = recall(query="", queries=["alpha", "beta"], limit=3, _memory_port=_Mem())
    assert not r.is_error
    assert "Q=alpha beta|L=3" in r.content


def test_recall_accepts_keywords_alias():
    class _Mem:
        def recall(self, query: str, *, scopes=None, principal=None, limit: int = 5) -> str:
            return f"Q={query}|L={limit}"

    r = recall(query="", keywords=["memory", "search"], limit=2, _memory_port=_Mem())
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
    assert "do not write new files to `/files/...`" in out.content
    assert "Existing `/files/{file_id}` URLs may be passed to `read_file`" in out.content
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


def test_skill_view_alias_loads_skill_instructions():
    from hushclaw.tools.builtins.skill_tools import skill_view

    class _Registry:
        def get(self, name: str):
            if name == "crm-operator":
                return {
                    "name": "crm-operator",
                    "content": "Use CRM workflow.",
                    "available": True,
                }
            return None

    out = skill_view("crm-operator", _skill_registry=_Registry())
    assert not out.is_error
    assert "# Skill: crm-operator" in out.content
    assert "Use CRM workflow." in out.content


def test_search_skills_returns_ranked_compact_candidates(tmp_path):
    from hushclaw.skills.loader import SkillRegistry
    from hushclaw.tools.builtins.skill_tools import search_skills

    class _Registry(SkillRegistry):
        def __init__(self):
            pass

    code_path = tmp_path / "code-review" / "SKILL.md"
    code_path.parent.mkdir()
    code_path.write_text("Body", encoding="utf-8")
    market_path = tmp_path / "market-research" / "SKILL.md"
    market_path.parent.mkdir()
    market_path.write_text("Body", encoding="utf-8")

    registry = _Registry()
    registry._skills = {}
    registry._skill_versions = {}
    registry._state = {"disabled": {}}
    registry.register_skill(
        "code-review",
        "Structured code review workflow",
        str(code_path),
    )
    registry._skills["code-review"]["tags"] = ["review", "coding"]
    registry.register_skill(
        "market-research",
        "Analyze market information",
        str(market_path),
    )

    out = search_skills("review code", limit=1, _skill_registry=registry)

    assert not out.is_error
    assert "matching skills for: review code" in out.content
    assert "- code-review" in out.content
    assert "market-research" not in out.content


def test_default_tool_registry_includes_read_artifact():
    from hushclaw.config.schema import ToolsConfig
    from hushclaw.tools.registry import TOOL_PROFILES

    assert "read_artifact" in ToolsConfig().enabled
    assert "search_skills" in ToolsConfig().enabled
    assert "skill_view" in ToolsConfig().enabled
    assert "search_skills" in TOOL_PROFILES["full"]
    assert "search_skills" in TOOL_PROFILES["coding"]
    for profile in TOOL_PROFILES.values():
        assert len(profile) == len(set(profile))
    assert "web_search" in ToolsConfig().enabled
    assert "edit_document" in ToolsConfig().enabled
    assert "apply_patch" not in ToolsConfig().enabled
    assert "apply_patch" not in TOOL_PROFILES["full"]
    assert "apply_patch" not in TOOL_PROFILES["coding"]


def test_remember_infers_memory_kind_from_note_type():
    from hushclaw.tools.builtins.memory_tools import remember

    class _Mem:
        def __init__(self):
            self.metadata = None

        def remember(self, content, *, scope="global", principal=None, metadata=None):
            self.metadata = metadata or {}
            return "note-12345678"

    mem = _Mem()
    out = remember(
        content="The user prefers concise answers",
        note_type="preference",
        _memory_port=mem,
        _config=SimpleNamespace(agent=SimpleNamespace(memory_scope="")),
    )
    assert not out.is_error
    assert mem.metadata["memory_kind"] == "user_model"
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


def test_parse_textual_dsml_tool_call_maps_to_edit_document_shape():
    text = '''现在我有完整上下文。
<｜DSML｜tool_calls>
<｜DSML｜invoke name="update_document">
<｜DSML｜parameter name="path" string="true">/tmp/report.md</｜DSML｜parameter>
<｜DSML｜parameter name="change_summary" string="true">补充章节</｜DSML｜parameter>
<｜DSML｜parameter name="operations" string="false">[{"op_type":"append_after","anchor":"## A","content":"\\nB"}]</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜tool_calls>'''

    content, calls = parse_textual_tool_calls(text)

    assert "DSML" not in content
    assert len(calls) == 1
    call = calls[0]
    assert call.name == "edit_document"
    assert call.input["path"] == "/tmp/report.md"
    assert call.input["operations"] == [{"anchor": "## A", "content": "\nB", "type": "append_after"}]


def test_parse_response_payload_falls_back_to_textual_tool_calls():
    data = {
        "choices": [{
            "message": {
                "content": '<invoke name="remember"><parameter name="content">hello</parameter></invoke>'
            }
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }

    content, calls, in_tok, out_tok, stop_reason = parse_response_payload(data)

    assert content == ""
    assert len(calls) == 1
    assert calls[0].name == "remember"
    assert calls[0].input == {"content": "hello"}
    assert (in_tok, out_tok) == (1, 2)
    assert stop_reason == "end_turn"


def test_parse_textual_tool_calls_strips_minimax_tail_marker():
    text = (
        '<invoke name="remember"><parameter name="content">hello</parameter></invoke>'
        '</minimax:tool_call>'
    )

    content, calls = parse_textual_tool_calls(text)

    assert content == ""
    assert len(calls) == 1
    assert calls[0].name == "remember"


def test_parse_textual_tool_calls_strips_orphan_tool_tail_tags():
    text = "</parameter>\n</tool_calls>\n</think>\nFinal answer."

    content, calls = parse_textual_tool_calls(text)

    assert calls == []
    assert content == "Final answer."
    assert "tool_calls" not in content
    assert "parameter" not in content
    assert "think" not in content


def test_parse_response_payload_prefers_native_tool_calls():
    data = {
        "choices": [{
            "message": {
                "content": '<invoke name="remember"><parameter name="content">bad</parameter></invoke>',
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "remember", "arguments": '{"content":"native"}'},
                }],
            }
        }],
        "usage": {},
    }

    content, calls, _in_tok, _out_tok, stop_reason = parse_response_payload(data)

    assert "invoke" in content
    assert len(calls) == 1
    assert calls[0].id == "call-1"
    assert calls[0].input == {"content": "native"}
    assert stop_reason == "end_turn"


def test_parse_response_payload_maps_openai_length_to_max_tokens():
    data = {
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "partial answer"},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }

    content, calls, in_tok, out_tok, stop_reason = parse_response_payload(data)

    assert content == "partial answer"
    assert calls == []
    assert (in_tok, out_tok) == (1, 2)
    assert stop_reason == "max_tokens"


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


def test_transsion_control_plane_router_normalization_logs_info_once(monkeypatch):
    import hushclaw.providers.transsion as transsion

    transsion._CONTROL_PLANE_REDIRECT_LOGGED = False
    calls = []
    monkeypatch.setattr(transsion.log, "info", lambda *args, **kwargs: calls.append(args))

    assert _normalize_router_base("https://bus-ie.aibotplatform.com/v1") == "https://airouter.aibotplatform.com/v1"
    assert _normalize_router_base("https://bus-ie.aibotplatform.com") == "https://airouter.aibotplatform.com/v1"

    assert len(calls) == 1
    assert "routing LLM calls via" in calls[0][0]


def test_builtin_system_tools():
    from hushclaw.tools.builtins.system_tools import get_time, platform_info
    r1 = get_time()
    assert not r1.is_error
    assert "T" in r1.content  # ISO format

    r2 = platform_info()
    assert not r2.is_error
    assert "python" in r2.content.lower()


def test_builtin_file_tools(tmp_path):
    from hushclaw.tools.builtins.file_tools import read_file, search_files, write_file, list_dir
    test_file = tmp_path / "test.txt"

    wr = write_file(str(test_file), "hello hushclaw")
    assert not wr.is_error

    sr = search_files("hushclaw", path=str(tmp_path), context_lines=0)
    assert not sr.is_error
    search_payload = json.loads(sr.content)
    assert search_payload["count"] == 1
    assert search_payload["matches"][0]["path"] == "test.txt"
    assert search_payload["matches"][0]["line"] == 1

    rd = read_file(str(test_file))
    assert not rd.is_error
    assert "hello hushclaw" in rd.content

    ld = list_dir(str(tmp_path))
    assert not ld.is_error
    assert "test.txt" in ld.content

    rd_missing = read_file(str(tmp_path / "missing.txt"))
    assert rd_missing.is_error


def test_search_files_supports_workspace_default_and_glob(tmp_path):
    from hushclaw.tools.builtins.file_tools import search_files

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "notes.md").write_text("Alpha\nNeedle in markdown\nOmega\n", encoding="utf-8")
    (workspace_dir / "code.py").write_text("needle in code\n", encoding="utf-8")
    cfg = SimpleNamespace(agent=SimpleNamespace(workspace_dir=workspace_dir))

    res = search_files("needle", path=".", file_glob="*.md", context_lines=1, _config=cfg)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["count"] == 1
    assert payload["matches"][0]["path"] == "notes.md"
    assert payload["matches"][0]["line"] == 2
    assert [row["line"] for row in payload["matches"][0]["context"]] == [1, 2, 3]


def test_session_search_discovery_and_browse(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.session_tools import session_search

    memory = MemoryStore(tmp_path / "memory", embed_provider="local")
    try:
        memory.save_turn("sess-search", "user", "We decided to use SQLite FTS for session search")
        memory.save_turn("sess-search", "assistant", "The implementation should browse exact evidence.")

        found = session_search("SQLite FTS", _memory_store=memory)
        assert not found.is_error
        assert found.metadata["items"][0]["session_id"] == "sess-search"

        browsed = session_search(mode="browse", session_id="sess-search", limit=1, _memory_store=memory)
        assert not browsed.is_error
        assert browsed.metadata["has_more"] is True
        assert browsed.metadata["next_cursor"]
    finally:
        memory.close()


def test_work_task_tools_create_claim_complete(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.taskrun_tools import (
        claim_work_task,
        complete_work_task,
        create_work_task,
        list_work_tasks,
    )

    memory = MemoryStore(tmp_path / "memory", embed_provider="local")
    try:
        created = create_work_task("Write verifier", spec="Check file mutations", _memory_store=memory)
        assert not created.is_error
        task = json.loads(created.content)
        assert task["status"] == "queued"

        listed = list_work_tasks(_memory_store=memory)
        assert "Write verifier" in listed.content

        claimed = claim_work_task(task["task_id"], worker_id="tester", _memory_store=memory)
        assert not claimed.is_error
        run = json.loads(claimed.content)
        assert run["status"] == "running"

        completed = complete_work_task(run["run_id"], "done", _memory_store=memory)
        assert not completed.is_error
        assert memory.get_task(task["task_id"])["status"] == "done"

        queued = create_work_task("Queued follow-up", _memory_store=memory)
        assert not queued.is_error
        done_listed = list_work_tasks(status="done", _memory_store=memory)
        assert "Write verifier" in done_listed.content
        assert "Queued follow-up" not in done_listed.content
    finally:
        memory.close()


def test_read_file_falls_back_to_workspace_files_for_relative_paths(tmp_path):
    from hushclaw.tools.builtins.file_tools import read_file, write_file

    workspace_dir = tmp_path / "workspace"
    cfg = SimpleNamespace(agent=SimpleNamespace(workspace_dir=workspace_dir))

    wr = write_file("ai_token_logic_final.md", "# Token Logic", _config=cfg)
    assert not wr.is_error

    rd = read_file("ai_token_logic_final.md", _config=cfg)
    assert not rd.is_error
    assert "# Token Logic" in rd.content


def test_read_file_resolves_files_url_to_uploaded_file(tmp_path):
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

        rd = read_file(f"/files/{wr.artifact_id}", _config=cfg, _memory_store=memory)
        assert not rd.is_error
        assert "# Token Logic" in rd.content
    finally:
        memory.close()


def test_patch_document_resolves_files_url_to_uploaded_file(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import patch_document, read_file, write_file

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        wr = write_file("notes.md", "Before\nTarget line\nAfter", _config=cfg, _memory_store=memory)
        assert not wr.is_error
        url = f"/files/{wr.artifact_id}"

        patched = patch_document(
            url,
            [{"type": "replace", "anchor": "Target line", "content": "Updated line"}],
            _config=cfg,
            _memory_store=memory,
            create_backup=False,
        )
        assert not patched.is_error

        rd = read_file(url, _config=cfg, _memory_store=memory)
        assert not rd.is_error
        assert "Updated line" in rd.content
    finally:
        memory.close()


def test_search_files_resolves_files_url_to_uploaded_file(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import search_files, write_file

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        wr = write_file("notes.md", "Before\nTarget line\nAfter", _config=cfg, _memory_store=memory)
        assert not wr.is_error

        res = search_files("Target", path=f"/files/{wr.artifact_id}", _config=cfg, _memory_store=memory)
        assert not res.is_error
        payload = json.loads(res.content)
        assert payload["count"] == 1
        assert payload["matches"][0]["line"] == 2
    finally:
        memory.close()


def test_write_file_normalizes_files_prefix_to_workspace_relative_path(tmp_path):
    from hushclaw.tools.builtins.file_tools import write_file

    workspace_dir = tmp_path / "workspace"
    cfg = SimpleNamespace(agent=SimpleNamespace(workspace_dir=workspace_dir))
    res = write_file("/files/reports/q1.txt", "hello", _config=cfg)
    assert not res.is_error
    assert "Normalized WebUI URL-like path" in res.content
    assert (workspace_dir / "files" / "reports" / "q1.txt").read_text(encoding="utf-8") == "hello"


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


def test_update_document_updates_existing_markdown_with_backup(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import update_document

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        target = workspace_dir / "files" / "report.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Report\n\nOld body\n", encoding="utf-8")
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        res = update_document(
            str(target),
            "# Report\n\nNew body",
            change_summary="Replace old body with new body",
            _config=cfg,
            _memory_store=memory,
        )

        assert not res.is_error
        payload = json.loads(res.content)
        assert payload["path"] == str(target)
        assert payload["old_sha256"] != payload["new_sha256"]
        assert payload["backup_path"]
        assert Path(payload["backup_path"]).exists()
        assert Path(payload["backup_path"]).read_text(encoding="utf-8") == "# Report\n\nOld body\n"
        assert payload["url"].startswith("/files/")
        assert "New body" in target.read_text(encoding="utf-8")
        assert "Generated by hushclaw" in target.read_text(encoding="utf-8")
    finally:
        memory.close()


def test_update_document_refuses_to_create_new_file(tmp_path):
    from hushclaw.tools.builtins.file_tools import update_document

    target = tmp_path / "missing.md"
    res = update_document(str(target), "# New")

    assert res.is_error
    assert "Document not found" in res.content
    assert not target.exists()


def test_update_document_rejects_sha_mismatch(tmp_path):
    from hushclaw.tools.builtins.file_tools import update_document

    target = tmp_path / "report.md"
    target.write_text("# Report\n", encoding="utf-8")

    res = update_document(str(target), "# Changed", expected_sha256="bad-sha")

    assert res.is_error
    assert "changed since it was read" in res.content
    assert target.read_text(encoding="utf-8") == "# Report\n"


def test_update_document_rejects_structured_formats_for_v1(tmp_path):
    from hushclaw.tools.builtins.file_tools import update_document

    target = tmp_path / "report.docx"
    target.write_bytes(b"not really docx")

    res = update_document(str(target), "content")

    assert res.is_error
    assert "Markdown, HTML, and plain text" in res.content


def test_edit_document_routes_operations_to_patch(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("Intro\n\n## A\n", encoding="utf-8")

    res = edit_document(
        str(target),
        operations=[{"op_type": "append_after", "anchor": "## A", "content": "\nAdded"}],
        change_summary="Append section",
        create_backup=False,
    )

    assert not res.is_error
    assert "## A\nAdded" in target.read_text(encoding="utf-8")


def test_edit_document_normalizes_operation_aliases(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("Intro\n\n## A\nOld paragraph\n", encoding="utf-8")

    res = edit_document(
        str(target),
        operations=[
            {"type": "", "operation": "append", "anchor": "", "target": "## A", "content": "\nAdded"},
            {"action": "replace", "old_text": "Old paragraph", "new_text": "New paragraph"},
        ],
        change_summary="Apply aliased operations",
        create_backup=False,
    )

    text = target.read_text(encoding="utf-8")
    assert not res.is_error
    assert "## A\nAdded" in text
    assert "New paragraph" in text
    assert "Old paragraph" not in text


def test_edit_document_infers_append_anchor_from_after_alias(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("# Title\n", encoding="utf-8")

    res = edit_document(
        str(target),
        operations=[{"after": "# Title", "content": "\nBody"}],
        create_backup=False,
    )

    assert not res.is_error
    assert "# Title\nBody" in target.read_text(encoding="utf-8")


def test_edit_document_infers_replace_from_target_and_content(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("# Title\nOld paragraph\n", encoding="utf-8")

    res = edit_document(
        str(target),
        operations=[{"target": "Old paragraph", "content": "New paragraph"}],
        create_backup=False,
    )

    text = target.read_text(encoding="utf-8")
    assert not res.is_error
    assert "New paragraph" in text
    assert "Old paragraph" not in text


def test_edit_document_infers_replace_from_anchor_and_content(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("# Title\nOld paragraph\n", encoding="utf-8")

    res = edit_document(
        str(target),
        operations=[{"anchor": "Old paragraph", "content": "New paragraph"}],
        create_backup=False,
    )

    text = target.read_text(encoding="utf-8")
    assert not res.is_error
    assert "New paragraph" in text
    assert "Old paragraph" not in text


def test_edit_document_routes_content_to_rewrite(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("# Old\n", encoding="utf-8")

    res = edit_document(str(target), content="# New", mode="rewrite", create_backup=False)

    assert not res.is_error
    text = target.read_text(encoding="utf-8")
    assert "# New" in text
    assert "Generated by hushclaw" in text


def test_edit_document_rejects_ambiguous_patch_and_rewrite(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    original = "# Old\n"
    target.write_text(original, encoding="utf-8")

    res = edit_document(
        str(target),
        content="# New",
        operations=[{"type": "replace", "anchor": "Old", "content": "New"}],
    )

    assert res.is_error
    assert "either operations" in res.content
    assert target.read_text(encoding="utf-8") == original


def test_edit_document_rejects_empty_edit(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document

    target = tmp_path / "report.md"
    target.write_text("# Old\n", encoding="utf-8")

    res = edit_document(str(target))

    assert res.is_error
    assert "No document edit" in res.content


def test_patch_document_supports_replace_append_prepend_delete(tmp_path):
    from hushclaw.tools.builtins.file_tools import patch_document

    target = tmp_path / "report.md"
    target.write_text("Intro\n\n## A\nOld paragraph\n\n## B\nRemove me\n", encoding="utf-8")

    res = patch_document(
        str(target),
        [
            {"type": "replace", "anchor": "Old paragraph", "content": "New paragraph"},
            {"type": "append_after", "anchor": "## A", "content": "\nAdded after A"},
            {"type": "prepend_before", "anchor": "## B", "content": "Before B\n"},
            {"type": "delete", "anchor": "Remove me\n"},
        ],
        change_summary="Patch sections A and B",
    )

    assert not res.is_error
    text = target.read_text(encoding="utf-8")
    assert "New paragraph" in text
    assert "## A\nAdded after A" in text
    assert "Before B\n## B" in text
    assert "Remove me" not in text


def test_patch_document_matches_markdown_table_anchor_with_spacing_drift(tmp_path):
    from hushclaw.tools.builtins.file_tools import patch_document

    target = tmp_path / "canvas.md"
    target.write_text(
        "| 商业画布模块 | 内容 |\n"
        "|:---|:---|\n"
        "| 核心资源 | 声学算法积累、多语种 ASR/TTS（尤其小语种）、翻译引擎、端侧部署经验、传音渠道关系、联发科芯片适配、中东数据通道 |\n"
        "| **关键合作伙伴** | 传音、联发科、渠道伙伴 |\n",
        encoding="utf-8",
    )

    anchor = (
        "| 核心资源 | 声学算法积累、多语种 ASR/TTS（尤其小语种）、翻译引擎、端侧部署经验、传音渠道关系、联发科芯片适配、中东数据通道 |\n"
        "| **关键合作伙伴** | 传音、联发科、渠道伙伴 |"
    )
    # Models often drift on Markdown table spacing; this should still match
    # because the normalized anchor remains unique.
    drifted_anchor = anchor.replace("| **关键合作伙伴** |", "|**关键合作伙伴**|")

    res = patch_document(
        str(target),
        [{"type": "replace", "anchor": drifted_anchor, "content": anchor + "\n| 渠道 | 预装和企业销售 |"}],
        create_backup=False,
    )

    text = target.read_text(encoding="utf-8")
    assert not res.is_error
    assert "| 渠道 | 预装和企业销售 |" in text


def test_patch_document_validation_failure_is_atomic(tmp_path):
    from hushclaw.tools.builtins.file_tools import patch_document

    target = tmp_path / "report.md"
    original = "Only once\nRepeated\nRepeated\n"
    target.write_text(original, encoding="utf-8")

    res = patch_document(
        str(target),
        [
            {"type": "replace", "anchor": "Only once", "content": "Changed"},
            {"type": "delete", "anchor": "Repeated"},
        ],
    )

    assert res.is_error
    assert "appears 2 times" in res.content
    assert target.read_text(encoding="utf-8") == original


def test_patch_document_rejects_sha_mismatch(tmp_path):
    from hushclaw.tools.builtins.file_tools import patch_document

    target = tmp_path / "report.txt"
    target.write_text("hello\n", encoding="utf-8")

    res = patch_document(
        str(target),
        [{"type": "replace", "anchor": "hello", "content": "bye"}],
        expected_sha256="bad-sha",
    )

    assert res.is_error
    assert "changed since it was read" in res.content
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_patch_document_creates_backup_and_refreshes_files_record(tmp_path):
    from hushclaw.memory.store import MemoryStore
    from hushclaw.tools.builtins.file_tools import patch_document

    memory = MemoryStore(data_dir=tmp_path / "memory")
    try:
        workspace_dir = tmp_path / "workspace"
        target = workspace_dir / "files" / "report.html"
        target.parent.mkdir(parents=True)
        target.write_text("<h1>Old</h1>", encoding="utf-8")
        cfg = SimpleNamespace(
            agent=SimpleNamespace(workspace_dir=workspace_dir),
            server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        res = patch_document(
            str(target),
            [{"type": "replace", "anchor": "Old", "content": "New"}],
            _config=cfg,
            _memory_store=memory,
        )

        assert not res.is_error
        payload = json.loads(res.content)
        assert payload["backup_path"]
        assert Path(payload["backup_path"]).exists()
        assert payload["url"].startswith("/files/")
        assert target.read_text(encoding="utf-8") == "<h1>New</h1>"
    finally:
        memory.close()


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
    assert captured["timeout"] == 15
    assert captured["context"] is sentinel_context


def test_web_search_uses_jina_search_with_ssl_context_and_api_key(monkeypatch):
    from hushclaw.tools.builtins.web_tools import web_search

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self, _size=-1):
            return json.dumps({
                "data": [{
                    "title": "On-device AI trends",
                    "url": "https://example.com/edge-ai",
                    "description": "Edge AI market overview",
                    "content": "Longer search result preview",
                    "publishedTime": "2026-01-02",
                }]
            }).encode()

    sentinel_context = object()

    def _fake_urlopen(req, timeout=None, context=None):
        captured["full_url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        captured["context"] = context
        return _Resp()

    monkeypatch.setattr("hushclaw.tools.builtins.web_tools.make_ssl_context", lambda: sentinel_context)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    cfg = SimpleNamespace(api_keys={"jina": "jina-test-key"})
    result = web_search("端侧 AI trend", limit=3, _config=cfg)

    assert not result.is_error
    assert captured["full_url"].startswith("https://s.jina.ai/")
    assert "%E7%AB%AF%E4%BE%A7%20AI%20trend" in captured["full_url"]
    assert captured["headers"]["Authorization"] == "Bearer jina-test-key"
    assert captured["timeout"] == 15
    assert captured["context"] is sentinel_context
    payload = json.loads(result.content)
    assert payload["provider"] == "jina_search"
    assert payload["results"][0]["url"] == "https://example.com/edge-ai"
    assert payload["results"][0]["published_at"] == "2026-01-02"


def test_web_search_falls_back_to_markdown_results(monkeypatch):
    from hushclaw.tools.builtins.web_tools import web_search

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self, _size=-1):
            return b"- [Result A](https://example.com/a) Useful snippet about AI.\n"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Resp())

    result = web_search("edge ai", limit=1)

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["results"] == [{
        "title": "Result A",
        "url": "https://example.com/a",
        "snippet": "Useful snippet about AI.",
        "content_preview": "Useful snippet about AI.",
        "published_at": "",
    }]


def test_web_read_tools_have_short_per_tool_timeout():
    from hushclaw.tools.builtins.web_tools import fetch_url, jina_read, web_search

    assert fetch_url._hushclaw_tool.timeout == 20
    assert jina_read._hushclaw_tool.timeout == 20
    assert web_search._hushclaw_tool.timeout == 20


def test_jina_read_rejects_search_result_pages():
    from hushclaw.tools.builtins.web_tools import jina_read

    result = jina_read('https://www.google.com/search?q=%22Muse+Spark%22+评价+反馈')
    assert result.is_error
    assert "Use web_search" in result.content


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

    result = jina_read('https://example.com/search?q=%22Muse+Spark%22+评价+反馈')
    assert not result.is_error
    assert "%E8%AF%84%E4%BB%B7" in captured["full_url"]
    assert "评价" not in captured["full_url"]


def test_jina_read_uses_configured_jina_api_key(monkeypatch):
    from hushclaw.tools.builtins.web_tools import jina_read

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def read(self, _size=-1): return b"# Clean markdown"

    def _fake_urlopen(req, timeout=None, context=None):
        captured["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = jina_read("https://example.com/article", _config=SimpleNamespace(api_keys={"jina": "jina-cfg-key"}))
    assert not result.is_error
    assert captured["headers"]["Authorization"] == "Bearer jina-cfg-key"


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
    assert "run_hierarchical" not in names


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
