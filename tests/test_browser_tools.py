"""Tests for browser tools and BrowserSession."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from ghostclaw.browser import BrowserSession
from ghostclaw.config.schema import BrowserConfig, Config
from ghostclaw.config.loader import load_config


# ---------------------------------------------------------------------------
# BrowserSession unit tests
# ---------------------------------------------------------------------------

def test_session_lazy_init():
    """Browser page must be None before any navigation call."""
    session = BrowserSession(headless=True, timeout_ms=5000)
    assert session._page is None
    assert not session.is_open
    assert session.current_url == ""


def test_browser_config_defaults():
    """BrowserConfig defaults should be headless=True, timeout=30."""
    cfg = BrowserConfig()
    assert cfg.headless is True
    assert cfg.timeout == 30


def test_browser_config_in_config():
    """Config should include a browser field with BrowserConfig defaults."""
    cfg = Config()
    assert hasattr(cfg, "browser")
    assert isinstance(cfg.browser, BrowserConfig)
    assert cfg.browser.headless is True
    assert cfg.browser.timeout == 30


def test_browser_config_load_config_defaults():
    """load_config() should return a Config with browser defaults when no TOML exists."""
    cfg = load_config()
    assert isinstance(cfg.browser, BrowserConfig)
    assert cfg.browser.headless is True


# ---------------------------------------------------------------------------
# browser_tools unit tests (no Playwright required)
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_no_browser_navigate_error():
    """browser_navigate should return a friendly error when _browser is None."""
    from ghostclaw.tools.builtins.browser_tools import browser_navigate
    result = _run(browser_navigate(url="https://example.com", _browser=None))
    assert result.is_error
    assert "Playwright" in result.content or "Browser session" in result.content


def test_no_browser_get_content_error():
    from ghostclaw.tools.builtins.browser_tools import browser_get_content
    result = _run(browser_get_content(_browser=None))
    assert result.is_error


def test_no_browser_click_error():
    from ghostclaw.tools.builtins.browser_tools import browser_click
    result = _run(browser_click(selector="button", _browser=None))
    assert result.is_error


def test_no_browser_fill_error():
    from ghostclaw.tools.builtins.browser_tools import browser_fill
    result = _run(browser_fill(selector="#q", value="hello", _browser=None))
    assert result.is_error


def test_no_browser_submit_error():
    from ghostclaw.tools.builtins.browser_tools import browser_submit
    result = _run(browser_submit(field_selector="#q", value="hello", submit_selector="button", _browser=None))
    assert result.is_error


def test_no_browser_screenshot_error():
    from ghostclaw.tools.builtins.browser_tools import browser_screenshot
    result = _run(browser_screenshot(_browser=None))
    assert result.is_error


def test_no_browser_evaluate_error():
    from ghostclaw.tools.builtins.browser_tools import browser_evaluate
    result = _run(browser_evaluate(js="1+1", _browser=None))
    assert result.is_error


def test_no_browser_close_error():
    from ghostclaw.tools.builtins.browser_tools import browser_close
    result = _run(browser_close(_browser=None))
    assert result.is_error


def test_navigate_invalid_url():
    """browser_navigate should reject non-http URLs even with a valid session."""
    from ghostclaw.tools.builtins.browser_tools import browser_navigate
    mock_browser = MagicMock()
    result = _run(browser_navigate(url="ftp://bad.url", _browser=mock_browser))
    assert result.is_error
    assert "http" in result.content


# ---------------------------------------------------------------------------
# Registry loads browser_tools (with graceful fallback)
# ---------------------------------------------------------------------------

def test_registry_loads_browser_tools():
    """ToolRegistry.load_builtins() should register browser tools when available."""
    from ghostclaw.tools.registry import ToolRegistry
    reg = ToolRegistry()
    # load all enabled (None = keep all)
    reg.load_builtins(enabled=None)
    names = {td.name for td in reg.list_tools()}
    # Browser tools should be present since ghostclaw.tools.builtins.browser_tools imports fine
    assert "browser_navigate" in names
    assert "browser_get_content" in names
    assert "browser_close" in names


def test_browser_tool_schema_hides_injected_params():
    """_browser, _config, _session_id params must not appear in the LLM-visible schema."""
    from ghostclaw.tools.builtins.browser_tools import browser_screenshot
    td = browser_screenshot._ghostclaw_tool
    props = td.parameters.get("properties", {})
    assert "_browser" not in props
    assert "_config" not in props
    assert "_session_id" not in props
    # 'selector' should be visible
    assert "selector" in props


# ---------------------------------------------------------------------------
# Accessibility snapshot tools (no Playwright required)
# ---------------------------------------------------------------------------

def test_no_browser_snapshot_error():
    from ghostclaw.tools.builtins.browser_tools import browser_snapshot
    result = _run(browser_snapshot(_browser=None))
    assert result.is_error
    assert "Browser session" in result.content or "Playwright" in result.content


def test_no_browser_click_ref_error():
    from ghostclaw.tools.builtins.browser_tools import browser_click_ref
    result = _run(browser_click_ref(ref=1, _browser=None))
    assert result.is_error


def test_no_browser_fill_ref_error():
    from ghostclaw.tools.builtins.browser_tools import browser_fill_ref
    result = _run(browser_fill_ref(ref=1, value="hello", _browser=None))
    assert result.is_error


def test_browser_snapshot_schema_hides_injected():
    from ghostclaw.tools.builtins.browser_tools import browser_snapshot
    props = browser_snapshot._ghostclaw_tool.parameters.get("properties", {})
    assert "_browser" not in props


def test_browser_click_ref_schema():
    from ghostclaw.tools.builtins.browser_tools import browser_click_ref
    td = browser_click_ref._ghostclaw_tool
    props = td.parameters.get("properties", {})
    assert "ref" in props
    assert "_browser" not in props
    assert "ref" in td.parameters.get("required", [])


def test_browser_fill_ref_schema():
    from ghostclaw.tools.builtins.browser_tools import browser_fill_ref
    td = browser_fill_ref._ghostclaw_tool
    props = td.parameters.get("properties", {})
    assert "ref" in props
    assert "value" in props
    assert "_browser" not in props


# ---------------------------------------------------------------------------
# Multi-tab tools (no Playwright required)
# ---------------------------------------------------------------------------

def test_no_browser_new_tab_error():
    from ghostclaw.tools.builtins.browser_tools import browser_new_tab
    result = _run(browser_new_tab(url="https://example.com", _browser=None))
    assert result.is_error


def test_no_browser_list_tabs_error():
    from ghostclaw.tools.builtins.browser_tools import browser_list_tabs
    result = _run(browser_list_tabs(_browser=None))
    assert result.is_error


def test_no_browser_focus_tab_error():
    from ghostclaw.tools.builtins.browser_tools import browser_focus_tab
    result = _run(browser_focus_tab(tab_id="abc123", _browser=None))
    assert result.is_error


def test_no_browser_close_tab_error():
    from ghostclaw.tools.builtins.browser_tools import browser_close_tab
    result = _run(browser_close_tab(tab_id="abc123", _browser=None))
    assert result.is_error


def test_new_tab_invalid_url():
    from ghostclaw.tools.builtins.browser_tools import browser_new_tab
    mock_browser = MagicMock()
    result = _run(browser_new_tab(url="ftp://bad.url", _browser=mock_browser))
    assert result.is_error
    assert "http" in result.content


def test_browser_new_tab_schema():
    from ghostclaw.tools.builtins.browser_tools import browser_new_tab
    props = browser_new_tab._ghostclaw_tool.parameters.get("properties", {})
    assert "url" in props
    assert "_browser" not in props


def test_browser_focus_tab_schema():
    from ghostclaw.tools.builtins.browser_tools import browser_focus_tab
    td = browser_focus_tab._ghostclaw_tool
    assert "tab_id" in td.parameters.get("required", [])


# ---------------------------------------------------------------------------
# Remote Chrome tool (no Playwright required)
# ---------------------------------------------------------------------------

def test_no_browser_connect_user_chrome_error():
    from ghostclaw.tools.builtins.browser_tools import browser_connect_user_chrome
    result = _run(browser_connect_user_chrome(_browser=None))
    assert result.is_error


def test_connect_user_chrome_invalid_url():
    from ghostclaw.tools.builtins.browser_tools import browser_connect_user_chrome
    mock_browser = MagicMock()
    result = _run(browser_connect_user_chrome(
        debugging_url="notaurl", _browser=mock_browser
    ))
    assert result.is_error
    assert "http" in result.content


def test_browser_connect_user_chrome_schema():
    from ghostclaw.tools.builtins.browser_tools import browser_connect_user_chrome
    props = browser_connect_user_chrome._ghostclaw_tool.parameters.get("properties", {})
    assert "debugging_url" in props
    assert "_browser" not in props


# ---------------------------------------------------------------------------
# Config: remote_debugging_url
# ---------------------------------------------------------------------------

def test_browser_config_remote_debugging_url_default():
    cfg = BrowserConfig()
    assert hasattr(cfg, "remote_debugging_url")
    assert cfg.remote_debugging_url == ""


# ---------------------------------------------------------------------------
# Registry: all new tools registered
# ---------------------------------------------------------------------------

def test_registry_loads_all_new_browser_tools():
    from ghostclaw.tools.registry import ToolRegistry
    reg = ToolRegistry()
    reg.load_builtins(enabled=None)
    names = {td.name for td in reg.list_tools()}
    new_tools = [
        "browser_snapshot", "browser_click_ref", "browser_fill_ref",
        "browser_new_tab", "browser_list_tabs", "browser_focus_tab", "browser_close_tab",
        "browser_connect_user_chrome",
    ]
    for name in new_tools:
        assert name in names, f"Expected {name!r} to be registered"


# ---------------------------------------------------------------------------
# BrowserSession unit tests for new attributes
# ---------------------------------------------------------------------------

def test_session_new_attributes():
    """BrowserSession should have _pages, _active_tab_id, _snapshot_map."""
    session = BrowserSession(headless=True, timeout_ms=5000)
    assert isinstance(session._pages, dict)
    assert session._active_tab_id == "default"
    assert isinstance(session._snapshot_map, dict)
    assert session.current_url == ""
