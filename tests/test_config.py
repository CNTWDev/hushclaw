"""Tests for configuration loading."""
import asyncio
import os
import sys
import tempfile
import json
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.config.defaults import DEFAULTS
from hushclaw.config.loader import load_config
from hushclaw.config.schema import Config
from hushclaw.prompts import build_system_prompt
from hushclaw.server.config_handler import handle_save_config


class _MockWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


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
    assert config.provider.name == "anthropic-raw"
    assert config.memory.data_dir is not None
    assert config.tools.timeout == 30


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


def test_toml_loading():
    with tempfile.TemporaryDirectory() as d:
        toml_path = Path(d) / ".hushclaw.toml"
        toml_path.write_text(
            '[agent]\nmodel = "claude-haiku-4-5-20251001"\nmax_tokens = 2048\n'
        )
        config = load_config(project_dir=Path(d))
        assert config.agent.model == "claude-haiku-4-5-20251001"
        assert config.agent.max_tokens == 2048


def test_gateway_agent_hierarchy_fields_toml_loading():
    with tempfile.TemporaryDirectory() as d:
        toml_path = Path(d) / ".hushclaw.toml"
        toml_path.write_text(
            '[gateway]\nshared_memory = true\n'
            '\n[[gateway.agents]]\n'
            'name = "commander"\n'
            'description = "Coordinator"\n'
            'role = "commander"\n'
            'team = "market"\n'
            'capabilities = ["dispatch", "synthesis"]\n'
            '\n[[gateway.agents]]\n'
            'name = "specialist"\n'
            'reports_to = "commander"\n'
            'role = "specialist"\n'
        )
        config = load_config(project_dir=Path(d))
        assert len(config.gateway.agents) == 2
        c0 = config.gateway.agents[0]
        c1 = config.gateway.agents[1]
        assert c0.role == "commander"
        assert c0.team == "market"
        assert c0.capabilities == ["dispatch", "synthesis"]
        assert c1.reports_to == "commander"


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
