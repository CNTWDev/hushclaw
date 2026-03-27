"""Tests for configuration loading."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.config.loader import load_config
from hushclaw.config.schema import Config


def test_default_config(monkeypatch, tmp_path):
    # Redirect user config/data dirs to an empty temp dir so the test is not
    # affected by any real hushclaw.toml present on the developer's machine.
    import hushclaw.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir",   lambda: tmp_path)
    config = load_config()
    assert isinstance(config, Config)
    assert config.agent.model == "claude-sonnet-4-6"
    assert config.agent.max_tokens == 4096
    assert config.provider.name == "anthropic-raw"
    assert config.memory.data_dir is not None
    assert config.tools.timeout == 30


def test_env_override(monkeypatch, tmp_path):
    # Isolate user config so env vars are not shadowed by a local TOML.
    import hushclaw.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir",   lambda: tmp_path)
    monkeypatch.setenv("HUSHCLAW_MODEL",     "claude-opus-4-6")
    # Use HUSHCLAW_API_KEY (always applied, no provider filter) so the test
    # works regardless of which provider is in the user's real config file.
    monkeypatch.setenv("HUSHCLAW_API_KEY", "test-key-123")
    config = load_config()
    assert config.agent.model == "claude-opus-4-6"
    assert config.provider.api_key == "test-key-123"


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
