"""Tests for configuration loading."""
import asyncio
import os
import sqlite3
import sys
import tempfile
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.config.defaults import DEFAULTS
from hushclaw.config.loader import load_config
from hushclaw.config.schema import AgentConfig, Config, ConfigError
from hushclaw.config.system_prompt import should_reset_persisted_system_prompt
from hushclaw.prompts import build_system_prompt
from hushclaw.server.config_handler import handle_save_config
from hushclaw.server.config_mixin import ConfigMixin


class _MockWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class _FakeConfigServer(ConfigMixin):
    def __init__(self, config: Config):
        self._gateway = SimpleNamespace(base_agent=SimpleNamespace(config=config))
        self._update_service = SimpleNamespace(
            current_version="test",
            last_result={},
            last_checked_at=0,
        )
        self._connectors = SimpleNamespace(status=lambda: {})
        self._playwright_available = False


def test_default_config(monkeypatch, tmp_path):
    # Redirect user config/data dirs to an empty temp dir so the test is not
    # affected by any real hushclaw.toml present on the developer's machine.
    import hushclaw.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir",   lambda: tmp_path)
    config = load_config()
    assert isinstance(config, Config)
    assert config.agent.model == "claude-sonnet-4-6"
    assert config.agent.max_tokens == 16384
    assert config.agent.stream_mode == "final_only"
    assert config.provider.name == "anthropic-raw"
    assert config.provider.timeout == 360
    assert config.memory.data_dir is not None
    assert config.tools.timeout == 30


def test_agent_stream_mode_validation():
    assert AgentConfig(stream_mode="final_only").stream_mode == "final_only"
    assert AgentConfig(stream_mode="always").stream_mode == "always"
    assert AgentConfig(stream_mode="off").stream_mode == "off"
    try:
        AgentConfig(stream_mode="sometimes")
    except ConfigError as exc:
        assert "stream_mode" in str(exc)
    else:
        raise AssertionError("invalid stream_mode should raise ConfigError")


def test_defaults_module_tracks_schema_defaults():
    assert DEFAULTS == asdict(Config())


def test_env_override(monkeypatch, tmp_path):
    # Isolate user config so env vars are not shadowed by a local TOML.
    import hushclaw.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir",   lambda: tmp_path)
    monkeypatch.setenv("HUSHCLAW_MODEL",     "claude-opus-4-6")
    # Use HUSHCLAW_API_KEY (always applied, no provider filter) so the test
    # works regardless of which provider is in the user's real config file.
    monkeypatch.setenv("HUSHCLAW_API_KEY", "test-key-123")
    monkeypatch.setenv("HUSHCLAW_PUBLIC_BASE_URL", "https://downloads.example.com")
    config = load_config()
    assert config.agent.model == "claude-opus-4-6"
    assert config.provider.api_key == "test-key-123"
    assert config.server.public_base_url == "https://downloads.example.com"


def test_config_status_requires_saved_provider_key(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123456")

    config = load_config()
    status = _FakeConfigServer(config)._config_status()

    assert status["api_key_set"] is True
    assert status["api_key_saved"] is False
    assert status["configured"] is False
    assert status["api_key_masked"] == ""


def test_config_status_accepts_explicit_saved_provider_key(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path)
    (tmp_path / "hushclaw.toml").write_text(
        '[provider]\napi_key = "saved-key-123456"\n',
        encoding="utf-8",
    )

    config = load_config()
    status = _FakeConfigServer(config)._config_status()

    assert status["api_key_set"] is True
    assert status["api_key_saved"] is True
    assert status["configured"] is True
    assert status["api_key_masked"] == "save…3456"


def test_toml_loading():
    with tempfile.TemporaryDirectory() as d:
        toml_path = Path(d) / ".hushclaw.toml"
        toml_path.write_text(
            '[agent]\nmodel = "claude-haiku-4-5-20251001"\nmax_tokens = 2048\n'
        )
        config = load_config(project_dir=Path(d))
        assert config.agent.model == "claude-haiku-4-5-20251001"
        assert config.agent.max_tokens == 2048


def test_gateway_agent_routing_tags_toml_loading():
    with tempfile.TemporaryDirectory() as d:
        toml_path = Path(d) / ".hushclaw.toml"
        toml_path.write_text(
            '[gateway]\nshared_memory = true\n'
            '\n[[gateway.agents]]\n'
            'name = "analyst"\n'
            'description = "Research analyst"\n'
            'routing_tags = ["research", "synthesis"]\n'
            '\n[[gateway.agents]]\n'
            'name = "writer"\n'
        )
        config = load_config(project_dir=Path(d))
        assert len(config.gateway.agents) == 2
        c0 = config.gateway.agents[0]
        c1 = config.gateway.agents[1]
        assert c0.routing_tags == ["research", "synthesis"]
        assert c1.routing_tags == []


def test_agentos_agent_schema_migration_backfills_opc_and_cleans_dynamic_agents(tmp_path):
    from hushclaw.config.migrations import migrate_agentos_agent_schema

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    (data_dir / "dynamic_agents.json").write_text(json.dumps([
        {
            "name": "market-researcher",
            "description": "Research markets",
            "role": "specialist",
            "team": "Growth",
            "reports_to": "ceo",
            "capabilities": ["research", "analysis"],
            "instructions": "Base instructions\n\n<!-- [hushclaw:org-context] -->\nold",
            "tools": ["web_search"],
        }
    ]), encoding="utf-8")

    result = migrate_agentos_agent_schema(config_dir=config_dir, data_dir=data_dir)

    assert str(data_dir / "dynamic_agents.json") in result["changed"]
    migrated = json.loads((data_dir / "dynamic_agents.json").read_text(encoding="utf-8"))
    assert migrated[0]["routing_tags"] == ["research", "analysis"]
    assert "role" not in migrated[0]
    assert "team" not in migrated[0]
    assert "reports_to" not in migrated[0]
    assert "capabilities" not in migrated[0]
    assert migrated[0]["instructions"] == "Base instructions"

    conn = sqlite3.connect(data_dir / "memory.db")
    try:
        row = conn.execute(
            "SELECT payload_json FROM opc_records WHERE record_type='employee' AND record_id='emp-market-researcher'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    employee = json.loads(row[0])
    assert employee["agent_name"] == "market-researcher"
    assert employee["role"] == "specialist"
    assert employee["team"] == "Growth"
    assert employee["reports_to"] == "ceo"
    assert employee["capabilities"] == ["research", "analysis"]


def test_agentos_agent_schema_migration_preserves_existing_opc_employee_fields(tmp_path):
    from hushclaw.config.migrations import migrate_agentos_agent_schema

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    (data_dir / "dynamic_agents.json").write_text(json.dumps([
        {
            "name": "operator",
            "description": "Old agent desc",
            "role": "specialist",
            "team": "Ops",
            "reports_to": "ceo",
            "capabilities": ["ops"],
        }
    ]), encoding="utf-8")
    conn = sqlite3.connect(data_dir / "memory.db")
    try:
        conn.execute(
            "CREATE TABLE opc_records (record_type TEXT NOT NULL, record_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY (record_type, record_id))"
        )
        conn.execute(
            "INSERT INTO opc_records (record_type, record_id, payload_json, created, updated) VALUES ('employee', 'emp-operator', ?, 1, 1)",
            (json.dumps({
                "agent_name": "operator",
                "display_name": "Ops Lead",
                "role": "operator",
                "team": "Existing Team",
                "reports_to": "founder",
                "capabilities": ["delivery"],
                "description": "Keep this",
                "status": "active",
            }),),
        )
        conn.commit()
    finally:
        conn.close()

    migrate_agentos_agent_schema(config_dir=config_dir, data_dir=data_dir)

    conn = sqlite3.connect(data_dir / "memory.db")
    try:
        row = conn.execute(
            "SELECT payload_json FROM opc_records WHERE record_type='employee' AND record_id='emp-operator'"
        ).fetchone()
    finally:
        conn.close()
    employee = json.loads(row[0])
    assert employee["display_name"] == "Ops Lead"
    assert employee["role"] == "operator"
    assert employee["team"] == "Existing Team"
    assert employee["reports_to"] == "founder"
    assert employee["capabilities"] == ["delivery"]
    assert employee["description"] == "Keep this"


def test_agentos_agent_schema_migration_discovers_workspace_dynamic_agents(tmp_path):
    from hushclaw.config.migrations import migrate_agentos_agent_schema

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    data_dir.mkdir()
    config_dir.mkdir()
    workspace.mkdir()
    (config_dir / "hushclaw.toml").write_text(
        f'[agent]\nworkspace_dir = "{workspace}"\n',
        encoding="utf-8",
    )
    (workspace / "dynamic_agents.json").write_text(json.dumps([
        {"name": "workspace-agent", "capabilities": "writing, review", "team": "Editorial"}
    ]), encoding="utf-8")

    result = migrate_agentos_agent_schema(config_dir=config_dir, data_dir=data_dir)

    assert str(workspace / "dynamic_agents.json") in result["changed"]
    migrated = json.loads((workspace / "dynamic_agents.json").read_text(encoding="utf-8"))
    assert migrated[0]["routing_tags"] == ["writing", "review"]
    assert "team" not in migrated[0]
    conn = sqlite3.connect(data_dir / "memory.db")
    try:
        row = conn.execute(
            "SELECT payload_json FROM opc_records WHERE record_type='employee' AND record_id='emp-workspace-agent'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_data_dir_env():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HUSHCLAW_DATA_DIR"] = d
        try:
            config = load_config()
            assert str(config.memory.data_dir) == d
        finally:
            del os.environ["HUSHCLAW_DATA_DIR"]


def test_windows_platform_dirs_use_appdata(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    appdata = tmp_path / "roaming"
    local_appdata = tmp_path / "local"
    monkeypatch.setattr(loader_mod.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    assert loader_mod._config_dir() == appdata / "hushclaw"
    assert loader_mod._data_dir() == local_appdata / "hushclaw"


def test_windows_default_skill_dir_uses_localappdata(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    appdata = tmp_path / "roaming"
    local_appdata = tmp_path / "local"
    monkeypatch.setattr(loader_mod.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    config = load_config(project_dir=tmp_path / "project")
    assert config.tools.skill_dir == local_appdata / "hushclaw" / "skills"


def test_explicit_workspace_dir_is_not_overwritten_by_default_resolution(tmp_path, monkeypatch):
    import hushclaw.config.loader as loader_mod

    project = tmp_path / "project"
    project.mkdir()
    workspace = tmp_path / "my-workspace"
    toml_path = project / ".hushclaw.toml"
    toml_path.write_text(f'[agent]\nworkspace_dir = "{workspace}"\n', encoding="utf-8")

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path / "data")

    config = load_config(project_dir=project)
    assert config.agent.workspace_dir == workspace


def test_bootstrap_workspace_migrates_legacy_memory_first_templates(tmp_path):
    import hushclaw.config.loader as loader_mod

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "SOUL.md").write_text(loader_mod._LEGACY_DEFAULT_SOUL_MD, encoding="utf-8")
    (ws / "AGENTS.md").write_text(loader_mod._LEGACY_DEFAULT_AGENTS_MD, encoding="utf-8")

    loader_mod._bootstrap_workspace(ws)

    soul_text = (ws / "SOUL.md").read_text(encoding="utf-8")
    agents_text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "Call `recall()` only for targeted follow-up searches" in soul_text
    assert "At the start of every conversation or task" not in soul_text
    assert "Treat recalled memory as supplemental context" in agents_text
    assert "proactively call recall()" not in agents_text


def test_bootstrap_workspace_preserves_custom_templates(tmp_path):
    import hushclaw.config.loader as loader_mod

    ws = tmp_path / "workspace"
    ws.mkdir()
    custom_soul = "# Agent Identity\n\nCustom memory policy.\n"
    custom_agents = "# Agent Behavior Rules\n\nNever overwrite this.\n"
    (ws / "SOUL.md").write_text(custom_soul, encoding="utf-8")
    (ws / "AGENTS.md").write_text(custom_agents, encoding="utf-8")

    loader_mod._bootstrap_workspace(ws)

    assert (ws / "SOUL.md").read_text(encoding="utf-8") == custom_soul
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == custom_agents


def test_bootstrap_workspace_seeds_user_md_without_migration_text(tmp_path):
    import hushclaw.config.loader as loader_mod

    ws = tmp_path / "workspace"
    loader_mod._bootstrap_workspace(ws)

    assert (ws / "USER.md").read_text(encoding="utf-8") == loader_mod._DEFAULT_USER_MD


def test_default_system_prompt_deemphasizes_opening_recall():
    prompt = build_system_prompt()
    assert "memory lookup is not the default first step" in prompt
    assert "Do NOT call recall() for short operational requests" in prompt
    assert "mandatory opening move" in prompt


def test_default_system_prompt_guides_context_personalization():
    prompt = build_system_prompt()
    assert "## Context Use" in prompt
    assert "User Profile Snapshot: adapt tone, depth, defaults" in prompt
    assert "Domain Beliefs: treat as the user's evolving judgment model" in prompt
    assert "Active Working State: treat as the highest-priority continuity signal" in prompt
    assert "Personalization should be visible in better defaults" in prompt
    assert "call recall() or session_search before asking them to repeat it" in prompt


def test_default_system_prompt_pauses_when_user_decision_is_needed():
    prompt = build_system_prompt()
    assert "If you need the user to make a decision" in prompt
    assert "stop this turn without calling tools" in prompt


def test_default_system_prompt_limits_skill_creation_and_allows_localized_skill_bodies():
    prompt = build_system_prompt()
    assert "scan the Skill Discovery" in prompt
    assert "search_skills(query)" in prompt
    assert "call use_skill(name)" in prompt
    assert "Use list_skills only for broad browsing" in prompt
    assert "explicitly asks you to save or create a skill" in prompt
    assert "validated at least twice" in prompt
    assert "search_files to locate unknown files or anchors" in prompt
    assert "write_file with relative paths" in prompt
    assert "edit_document for edits to existing Markdown/HTML/text documents" in prompt
    assert "new writes should use relative paths" in prompt
    assert "Skill bodies are an exception" in prompt
    assert "best fits their intended use" in prompt


def test_default_system_prompt_prefers_workspace_relative_output_paths():
    prompt = build_system_prompt()
    assert "prefer relative paths such as 'report.md'" in prompt
    assert "Do not choose '~/Desktop', '~/Downloads'" in prompt


def test_persisted_builtin_system_prompt_resets_to_code_default(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path)
    (tmp_path / "hushclaw.toml").write_text(
        "[agent]\n"
        f"system_prompt = {json.dumps(build_system_prompt())}\n",
        encoding="utf-8",
    )

    config = load_config()
    status = _FakeConfigServer(config)._config_status()

    assert config.agent.system_prompt == build_system_prompt()
    assert status["system_prompt_custom"] is False


def test_legacy_default_system_prompt_resets_to_code_default(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    legacy_prompt = "\n".join([
        "legacy default prompt",
        "memory lookup is not the default first step",
        "Do NOT call recall() for short operational requests",
        "x" * 1300,
    ])

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path)
    (tmp_path / "hushclaw.toml").write_text(
        "[agent]\n"
        f"system_prompt = {json.dumps(legacy_prompt)}\n",
        encoding="utf-8",
    )

    config = load_config()
    status = _FakeConfigServer(config)._config_status()

    assert should_reset_persisted_system_prompt(legacy_prompt) is True
    assert config.agent.system_prompt == build_system_prompt()
    assert status["system_prompt_custom"] is False


def test_save_config_clears_existing_builtin_system_prompt(monkeypatch, tmp_path):
    import tomllib
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    cfg_file = tmp_path / "hushclaw.toml"
    cfg_file.write_text(
        "[agent]\n"
        "model = \"claude-sonnet-4-6\"\n"
        f"system_prompt = {json.dumps(build_system_prompt())}\n",
        encoding="utf-8",
    )

    ws = _MockWs()
    asyncio.run(handle_save_config(
        ws,
        {
            "save_client_id": "sv_test_clear_builtin_prompt",
            "config": {"agent": {"workspace_dir": ""}},
        },
        lambda: None,
    ))

    assert ws.sent[-1]["ok"] is True
    with open(cfg_file, "rb") as f:
        saved = tomllib.load(f)
    assert saved["agent"]["model"] == "claude-sonnet-4-6"
    assert "system_prompt" not in saved["agent"]


def test_save_config_empty_system_prompt_removes_custom_prompt(monkeypatch, tmp_path):
    import tomllib
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    cfg_file = tmp_path / "hushclaw.toml"
    cfg_file.write_text(
        "[agent]\n"
        "system_prompt = \"custom prompt\"\n",
        encoding="utf-8",
    )

    ws = _MockWs()
    asyncio.run(handle_save_config(
        ws,
        {
            "save_client_id": "sv_test_clear_custom_prompt",
            "config": {"agent": {"system_prompt": ""}},
        },
        lambda: None,
    ))

    assert ws.sent[-1]["ok"] is True
    with open(cfg_file, "rb") as f:
        saved = tomllib.load(f)
    assert "system_prompt" not in saved["agent"]


def test_doctor_checks_existing_memory_db_writability(monkeypatch, tmp_path, capsys):
    from hushclaw.cli import setup as setup_mod
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path / "data")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite3.connect(data_dir / "memory.db").close()

    class _ReadonlyConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: _ReadonlyConnection())

    rc = setup_mod.cmd_doctor(object())
    out = capsys.readouterr().out
    assert rc == 1
    assert "memory.db not writable" in out
    assert "attempt to write a readonly database" in out


def test_storage_error_prints_memory_database_details(capsys, tmp_path):
    from hushclaw.cli import _handle_agent_init_error
    from hushclaw.memory.db import MemoryDatabaseError

    err = MemoryDatabaseError(
        "could not initialize memory database: no such column: scope",
        data_dir=tmp_path,
        db_path=tmp_path / "memory.db",
        backup_path=tmp_path / "backups" / "memory-db" / "memory-20260101-000000.db",
        cause=sqlite3.OperationalError("no such column: scope"),
    )

    _handle_agent_init_error(err)
    err_out = capsys.readouterr().err
    assert "[Storage Error]" in err_out
    assert "Detail: no such column: scope" in err_out
    assert f"data dir: {tmp_path}" in err_out
    assert f"database: {tmp_path / 'memory.db'}" in err_out
    assert "backup:" in err_out


def test_default_memory_after_tasks_prompt_avoids_desktop_bias():
    import hushclaw.config.loader as loader_mod

    assert "~/Desktop/" not in loader_mod._MEMORY_AFTER_TASKS
    assert "workspace/files" in loader_mod._MEMORY_AFTER_TASKS
    assert "Desktop or Downloads" in loader_mod._MEMORY_AFTER_TASKS_AGENTS


def test_save_config_migrates_legacy_single_account_sections(monkeypatch, tmp_path):
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    cfg_file = tmp_path / "hushclaw.toml"
    cfg_file.write_text(
        '[email]\n'
        'label = "Work"\n'
        'enabled = true\n'
        'username = "user@example.com"\n'
        'password = "email-secret"\n'
        '\n'
        '[calendar]\n'
        'label = "Work Cal"\n'
        'enabled = true\n'
        'username = "calendar@example.com"\n'
        'password = "calendar-secret"\n',
        encoding="utf-8",
    )

    ws = _MockWs()
    asyncio.run(handle_save_config(
        ws,
        {
            "save_client_id": "sv_test_legacy_accounts",
            "config": {
                "email": [{
                    "label": "Work",
                    "enabled": True,
                    "username": "user@example.com",
                }],
                "calendar": [{
                    "label": "Work Cal",
                    "enabled": True,
                    "username": "calendar@example.com",
                }],
            },
        },
        lambda: None,
    ))

    assert ws.sent
    assert ws.sent[-1]["type"] == "config_saved"
    assert ws.sent[-1]["ok"] is True

    import tomllib
    with open(cfg_file, "rb") as f:
        saved = tomllib.load(f)

    assert isinstance(saved["email"], list)
    assert saved["email"][0]["password"] == "email-secret"
    assert isinstance(saved["calendar"], list)
    assert saved["calendar"][0]["password"] == "calendar-secret"


def test_save_app_connector_token_uses_secret_store(monkeypatch, tmp_path):
    import tomllib
    import hushclaw.config.loader as loader_mod
    import hushclaw.secrets.store as secret_store_mod

    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(loader_mod, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(secret_store_mod, "get_data_dir", lambda: tmp_path / "data")

    ws = _MockWs()
    asyncio.run(handle_save_config(
        ws,
        {
            "save_client_id": "sv_test_app_connector",
            "config": {
                "app_connectors": {
                    "broker_base_url": "https://broker.example.com/oauth",
                    "github": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "client_id": "github-client",
                        "client_secret": "github-secret",
                        "token_ref": "app_connectors.github.token",
                        "token": "ghp_test_secret",
                        "default_repo": "owner/repo",
                        "allow_actions": True,
                    },
                    "google_workspace": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "client_id": "google-client",
                        "client_secret": "google-secret",
                        "refresh_token": "google-refresh",
                    },
                    "notion": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "client_id": "notion-client",
                        "client_secret": "notion-secret-client",
                        "workspace_name": "Docs",
                        "token": "notion-secret",
                    },
                    "jira": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "site_url": "https://example.atlassian.net",
                        "email": "user@example.com",
                        "client_id": "jira-client",
                        "client_secret": "jira-secret-client",
                        "token": "jira-secret",
                    },
                    "reddit": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "client_id": "reddit-client",
                        "client_secret": "reddit-secret-client",
                        "access_token": "reddit-access",
                        "refresh_token": "reddit-refresh",
                        "default_subreddit": "hushclaw",
                    },
                    "x": {
                        "enabled": True,
                        "auth_mode": "custom",
                        "client_id": "x-client",
                        "client_secret": "x-secret-client",
                        "bearer_token": "x-bearer",
                        "access_token": "x-access",
                    },
                }
            },
        },
        lambda: None,
    ))

    assert ws.sent[-1]["ok"] is True
    cfg_file = tmp_path / "hushclaw.toml"
    with open(cfg_file, "rb") as f:
        saved = tomllib.load(f)

    gh = saved["app_connectors"]["github"]
    assert saved["app_connectors"]["broker_base_url"] == "https://broker.example.com/oauth"
    assert gh["enabled"] is True
    assert gh["auth_mode"] == "custom"
    assert gh["token_ref"] == "app_connectors.github.token"
    assert gh["default_repo"] == "owner/repo"
    assert "token" not in gh
    assert "client_secret" not in gh
    assert "client_secret" not in saved["app_connectors"]["google_workspace"]
    assert "token" not in saved["app_connectors"]["notion"]
    assert "client_secret" not in saved["app_connectors"]["notion"]
    assert "token" not in saved["app_connectors"]["jira"]
    assert "client_secret" not in saved["app_connectors"]["jira"]
    assert saved["app_connectors"]["jira"]["site_url"] == "https://example.atlassian.net"
    assert "access_token" not in saved["app_connectors"]["reddit"]
    assert "client_secret" not in saved["app_connectors"]["reddit"]
    assert saved["app_connectors"]["reddit"]["default_subreddit"] == "hushclaw"
    assert "bearer_token" not in saved["app_connectors"]["x"]
    assert "access_token" not in saved["app_connectors"]["x"]
    assert "client_secret" not in saved["app_connectors"]["x"]

    secret_file = tmp_path / "data" / "secrets.json"
    assert secret_file.exists()
    secret_text = secret_file.read_text(encoding="utf-8")
    assert "ghp_test_secret" in secret_text
    assert "google-secret" in secret_text
    assert "github-secret" in secret_text
    assert "notion-secret-client" in secret_text
    assert "jira-secret-client" in secret_text
    assert "notion-secret" in secret_text
    assert "jira-secret" in secret_text
    assert "reddit-access" in secret_text
    assert "reddit-refresh" in secret_text
    assert "x-bearer" in secret_text
    assert "x-access" in secret_text


def test_load_app_connector_config_from_toml(tmp_path, monkeypatch):
    import hushclaw.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path / "data")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".hushclaw.toml").write_text(
        '[app_connectors.github]\n'
        'enabled = true\n'
        'auth_mode = "custom"\n'
        'client_id_ref = "custom.github.client_id"\n'
        'token_ref = "custom.github.token"\n'
        'default_repo = "owner/repo"\n'
        'allow_actions = false\n'
        '\n[app_connectors.google_workspace]\n'
        'enabled = true\n'
        'scopes = ["drive", "gmail"]\n'
        '\n[app_connectors.notion]\n'
        'enabled = true\n'
        'client_id_ref = "custom.notion.client_id"\n'
        'workspace_name = "Docs"\n'
        '\n[app_connectors.jira]\n'
        'enabled = true\n'
        'site_url = "https://example.atlassian.net"\n'
        'email = "user@example.com"\n'
        'client_id_ref = "custom.jira.client_id"\n'
        '\n[app_connectors.reddit]\n'
        'enabled = true\n'
        'access_token_ref = "custom.reddit.access"\n'
        'default_subreddit = "hushclaw"\n'
        '\n[app_connectors.x]\n'
        'enabled = true\n'
        'bearer_token_ref = "custom.x.bearer"\n',
        encoding="utf-8",
    )

    config = load_config(project_dir=project)
    gh = config.app_connectors.github
    assert gh.enabled is True
    assert gh.auth_mode == "custom"
    assert gh.client_id_ref == "custom.github.client_id"
    assert gh.token_ref == "custom.github.token"
    assert gh.default_repo == "owner/repo"
    assert gh.allow_actions is False
    assert config.app_connectors.google_workspace.enabled is True
    assert config.app_connectors.google_workspace.scopes == ["drive", "gmail"]
    assert config.app_connectors.notion.workspace_name == "Docs"
    assert config.app_connectors.notion.client_id_ref == "custom.notion.client_id"
    assert config.app_connectors.jira.site_url == "https://example.atlassian.net"
    assert config.app_connectors.jira.client_id_ref == "custom.jira.client_id"
    assert config.app_connectors.reddit.enabled is True
    assert config.app_connectors.reddit.access_token_ref == "custom.reddit.access"
    assert config.app_connectors.reddit.default_subreddit == "hushclaw"
    assert config.app_connectors.x.enabled is True
    assert config.app_connectors.x.bearer_token_ref == "custom.x.bearer"


def test_app_connector_oauth_google_flow_persists_tokens(monkeypatch, tmp_path):
    import json as json_mod
    import tomllib
    import hushclaw.config.loader as loader_mod
    import hushclaw.secrets.store as secret_store_mod
    import hushclaw.app_connectors.oauth as oauth_mod
    from hushclaw.config.schema import Config, GoogleWorkspaceAppConnectorConfig, AppConnectorsConfig
    from hushclaw.secrets import FileSecretStore

    monkeypatch.setattr(loader_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(loader_mod, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(secret_store_mod, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(oauth_mod, "_form_request", lambda *a, **k: {
        "access_token": "google-access",
        "refresh_token": "google-refresh",
    })

    cfg = Config(app_connectors=AppConnectorsConfig(
        google_workspace=GoogleWorkspaceAppConnectorConfig(
            client_id_ref="gw.client",
            client_secret_ref="gw.secret",
            access_token_ref="gw.access",
            refresh_token_ref="gw.refresh",
            auth_mode="custom",
            scopes=["drive.readonly"],
        )
    ))
    secrets = FileSecretStore(tmp_path / "data" / "secrets.json")
    secrets.set("gw.client", "client-id")
    secrets.set("gw.secret", "client-secret")

    start = oauth_mod.begin_oauth("google_workspace", cfg, secrets, "https://app.example.com")
    assert "accounts.google.com" in start.authorization_url
    state_payload = json_mod.loads(secrets.get(f"{oauth_mod.STATE_PREFIX}{start.state}"))
    assert state_payload["connector"] == "google_workspace"
    assert state_payload["redirect_uri"] == "https://app.example.com/oauth/app-connectors/google_workspace/callback"

    updates = oauth_mod.complete_oauth("google_workspace", "code", start.state, cfg, secrets)
    assert updates == {"enabled": True, "auth_mode": "custom", "auth_type": "oauth"}
    assert secrets.get("gw.access") == "google-access"
    assert secrets.get("gw.refresh") == "google-refresh"

    oauth_mod.persist_connector_updates("google_workspace", updates)
    with open(tmp_path / "hushclaw.toml", "rb") as f:
        saved = tomllib.load(f)
    assert saved["app_connectors"]["google_workspace"]["enabled"] is True
    assert saved["app_connectors"]["google_workspace"]["auth_type"] == "oauth"


def test_app_connector_managed_oauth_uses_broker_and_local_custody(monkeypatch, tmp_path):
    import json as json_mod
    import hushclaw.app_connectors.oauth as oauth_mod
    from hushclaw.config.schema import Config, GoogleWorkspaceAppConnectorConfig, AppConnectorsConfig
    from hushclaw.secrets import FileSecretStore

    calls = []

    def fake_json_request(url, *, method="GET", headers=None, data=None):
        calls.append((url, method, data))
        if url.endswith("/google_workspace/start"):
            return {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?state=" + data["state"]}
        if url.endswith("/google_workspace/handoff/exchange"):
            return {
                "access_token": "managed-access",
                "refresh_token": "managed-refresh",
                "auth_type": "oauth",
            }
        raise AssertionError(url)

    monkeypatch.setattr(oauth_mod, "_json_request", fake_json_request)

    cfg = Config(app_connectors=AppConnectorsConfig(
        broker_base_url="https://broker.example.com/oauth",
        google_workspace=GoogleWorkspaceAppConnectorConfig(
            auth_mode="managed",
            access_token_ref="gw.access",
            refresh_token_ref="gw.refresh",
        ),
    ))
    secrets = FileSecretStore(tmp_path / "secrets.json")

    start = oauth_mod.begin_oauth("google_workspace", cfg, secrets, "https://local.example.com")
    assert start.mode == "managed"
    assert start.authorization_url.startswith("https://accounts.google.com")
    state_payload = json_mod.loads(secrets.get(f"{oauth_mod.STATE_PREFIX}{start.state}"))
    assert state_payload["mode"] == "managed"
    assert calls[0][0] == "https://broker.example.com/oauth/google_workspace/start"
    assert calls[0][2]["redirect_uri"] == "https://local.example.com/oauth/app-connectors/google_workspace/callback"

    updates = oauth_mod.complete_oauth("google_workspace", "handoff-123", start.state, cfg, secrets)
    assert updates == {"enabled": True, "auth_mode": "managed", "auth_type": "oauth"}
    assert secrets.get("gw.access") == "managed-access"
    assert secrets.get("gw.refresh") == "managed-refresh"
    assert calls[1][0] == "https://broker.example.com/oauth/google_workspace/handoff/exchange"
    assert calls[1][2]["handoff_code"] == "handoff-123"
