"""Browser tools: Playwright-powered browser automation for HushClaw agents."""
from __future__ import annotations

import time
from pathlib import Path

from hushclaw.tools.base import tool, ToolResult

_INSTALL_HINT = (
    "Playwright is not installed. "
    "Run: pip install hushclaw[browser] && playwright install chromium"
)


def _no_browser() -> ToolResult:
    return ToolResult.error(
        "Browser session is not available. "
        "Ensure Playwright is installed: pip install hushclaw[browser] && playwright install chromium"
    )


@tool(
    name="browser_navigate",
    description="Open a URL in the browser and wait for the page to fully load. "
                "Returns the final URL (after any redirects). "
                "Supports http://, https://, and about: URLs (e.g. about:blank).",
)
async def browser_navigate(url: str, _browser=None) -> ToolResult:
    """Navigate to a URL and wait for network idle."""
    if _browser is None:
        return _no_browser()
    if not url.startswith(("http://", "https://", "about:")):
        return ToolResult.error(f"Invalid URL (must start with http://, https://, or about:): {url}")
    try:
        final_url = await _browser.navigate(url)
        return ToolResult.ok(f"Navigated to: {final_url}")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Navigation failed: {e}")


@tool(
    name="browser_get_content",
    description="Get the rendered text or HTML content of the current page (or a CSS selector element). "
                "Use as_text=true (default) for readable text, false for raw HTML.",
)
async def browser_get_content(
    selector: str = "body",
    as_text: bool = True,
    _browser=None,
) -> ToolResult:
    """Return rendered content of the current page."""
    if _browser is None:
        return _no_browser()
    try:
        content = await _browser.content(selector=selector, as_text=as_text)
        if not content:
            return ToolResult.ok("[Empty content]")
        # Truncate very long content
        if len(content) > 50_000:
            content = content[:50_000] + "\n[Content truncated at 50000 chars]"
        return ToolResult.ok(content)
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to get content: {e}")


@tool(
    name="browser_click",
    description="Click an element on the current page using a CSS selector.",
)
async def browser_click(selector: str, _browser=None) -> ToolResult:
    """Click an element identified by a CSS selector."""
    if _browser is None:
        return _no_browser()
    try:
        await _browser.click(selector)
        return ToolResult.ok(f"Clicked: {selector}")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to click '{selector}': {e}")


@tool(
    name="browser_fill",
    description="Fill an input field on the current page with a value. "
                "Use a CSS selector to identify the field.",
)
async def browser_fill(selector: str, value: str, _browser=None) -> ToolResult:
    """Fill an input element with a value."""
    if _browser is None:
        return _no_browser()
    try:
        await _browser.fill(selector, value)
        return ToolResult.ok(f"Filled '{selector}' with value")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to fill '{selector}': {e}")


@tool(
    name="browser_submit",
    description="Fill a form field and then click a submit button — combines fill + click in one step. "
                "Useful for login forms, search boxes, etc.",
)
async def browser_submit(
    field_selector: str,
    value: str,
    submit_selector: str,
    _browser=None,
) -> ToolResult:
    """Fill a field and click a submit button."""
    if _browser is None:
        return _no_browser()
    try:
        await _browser.fill(field_selector, value)
        await _browser.click(submit_selector)
        return ToolResult.ok(
            f"Filled '{field_selector}' and clicked '{submit_selector}'. "
            f"Current URL: {_browser.current_url}"
        )
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Submit failed: {e}")


@tool(
    name="browser_screenshot",
    description="Take a screenshot of the current page (or a specific element via CSS selector). "
                "Returns the file path of the saved PNG image.",
)
async def browser_screenshot(
    selector: str = "",
    _browser=None,
    _config=None,
    _session_id: str = "",
) -> ToolResult:
    """Take a screenshot and return the saved file path."""
    if _browser is None:
        return _no_browser()
    try:
        # Determine save directory
        if _config is not None and _config.memory.data_dir is not None:
            save_dir = Path(_config.memory.data_dir) / "screenshots"
        else:
            import tempfile
            save_dir = Path(tempfile.gettempdir()) / "hushclaw_screenshots"

        session_prefix = (_session_id or "")[:8] or "browser"
        stem = f"{session_prefix}-{int(time.time())}"

        path = await _browser.screenshot(
            selector=selector,
            save_dir=save_dir,
            stem=stem,
        )
        return ToolResult.ok(f"Screenshot saved: {path}")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Screenshot failed: {e}")


@tool(
    name="browser_evaluate",
    description="Execute JavaScript in the current page and return the result as a string. "
                "Useful for extracting dynamic data or triggering page actions.",
)
async def browser_evaluate(js: str, _browser=None) -> ToolResult:
    """Execute JavaScript in the page context."""
    if _browser is None:
        return _no_browser()
    try:
        result = await _browser.evaluate(js)
        return ToolResult.ok(result if result else "[No return value]")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"JavaScript evaluation failed: {e}")


@tool(
    name="browser_close",
    description="Close the browser and release all associated resources. "
                "Call this when you are done with browser automation.",
)
async def browser_close(_browser=None) -> ToolResult:
    """Close the browser session."""
    if _browser is None:
        return _no_browser()
    try:
        await _browser.close()
        return ToolResult.ok("Browser closed.")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to close browser: {e}")


@tool(
    name="browser_open_for_user",
    description=(
        "Open a visible browser window for the user to handle sensitive operations "
        "(login, payment, CAPTCHA, etc.) directly. The AI never sees the credentials. "
        "After calling this, call browser_wait_for_user to pause until the user is done."
    ),
)
async def browser_open_for_user(
    reason: str,
    _browser=None,
    _session_id: str = "",
    _handover_registry=None,
) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        url = await _browser.open_for_user()
        if _handover_registry is not None:
            import asyncio
            _handover_registry[_session_id] = asyncio.Event()
        return ToolResult.ok(
            f"Browser window opened at {url!r}. Reason: {reason}. "
            "The user can now interact directly with the page. "
            "Call browser_wait_for_user to wait until they finish."
        )
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to open browser for user: {e}")


@tool(
    name="browser_wait_for_user",
    description=(
        "Wait for the user to complete their action in the visible browser window "
        "(after browser_open_for_user). Blocks until the user clicks 'Done' in the UI, "
        "then closes the visible window and restores the session with updated cookies."
    ),
    timeout=0,  # no timeout — waits for user signal
)
async def browser_wait_for_user(
    wait_seconds: int = 300,
    _browser=None,
    _session_id: str = "",
    _handover_registry=None,
) -> ToolResult:
    if _browser is None:
        return _no_browser()
    import asyncio
    event = (_handover_registry or {}).get(_session_id)
    if event is None:
        return ToolResult.error(
            "No pending handover for this session. Call browser_open_for_user first."
        )
    try:
        await asyncio.wait_for(event.wait(), timeout=wait_seconds if wait_seconds > 0 else None)
    except asyncio.TimeoutError:
        pass  # timeout — close headed browser anyway
    finally:
        if _handover_registry and _session_id in _handover_registry:
            del _handover_registry[_session_id]
    try:
        await _browser.close_user_session()
        return ToolResult.ok("User action complete. Browser window closed. Cookies synced.")
    except Exception as e:
        return ToolResult.error(f"Error closing user session: {e}")


# ---------------------------------------------------------------------------
# Accessibility snapshot system (OpenClaw-inspired)
# ---------------------------------------------------------------------------

@tool(
    name="browser_snapshot",
    description=(
        "Scan the current page for visible interactive elements and return a compact "
        "numbered list (e.g. '[1] button \"Sign In\"'). Assigns stable ref numbers "
        "for use with browser_click_ref and browser_fill_ref. "
        "Much more token-efficient than browser_get_content for navigation tasks."
    ),
)
async def browser_snapshot(_browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        result = await _browser.snapshot()
        return ToolResult.ok(result)
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Snapshot failed: {e}")


@tool(
    name="browser_click_ref",
    description=(
        "Click the element identified by a ref number from the last browser_snapshot call. "
        "More reliable than CSS selectors — use browser_snapshot first to get ref numbers."
    ),
)
async def browser_click_ref(ref: int, _browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        await _browser.click_ref(ref)
        return ToolResult.ok(f"Clicked ref [{ref}].")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except ValueError as e:
        return ToolResult.error(str(e))
    except Exception as e:
        return ToolResult.error(f"Failed to click ref [{ref}]: {e}")


@tool(
    name="browser_fill_ref",
    description=(
        "Fill an input field identified by a ref number from the last browser_snapshot call. "
        "More reliable than CSS selectors — use browser_snapshot first to get ref numbers."
    ),
)
async def browser_fill_ref(ref: int, value: str, _browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        await _browser.fill_ref(ref, value)
        return ToolResult.ok(f"Filled ref [{ref}] with value.")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except ValueError as e:
        return ToolResult.error(str(e))
    except Exception as e:
        return ToolResult.error(f"Failed to fill ref [{ref}]: {e}")


# ---------------------------------------------------------------------------
# Multi-tab support
# ---------------------------------------------------------------------------

@tool(
    name="browser_new_tab",
    description=(
        "Open a new browser tab, optionally navigating to a URL. "
        "Returns a tab_id you can use with browser_focus_tab and browser_close_tab. "
        "The new tab becomes the active page."
    ),
)
async def browser_new_tab(url: str = "", _browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    if url and not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
    try:
        tab_id = await _browser.new_tab(url)
        msg = f"Opened new tab (tab_id={tab_id!r})"
        if url:
            msg += f" at {url}"
        return ToolResult.ok(msg)
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to open new tab: {e}")


@tool(
    name="browser_list_tabs",
    description=(
        "List all open browser tabs with their tab_id, URL, and title. "
        "Use tab_id values with browser_focus_tab and browser_close_tab."
    ),
)
async def browser_list_tabs(_browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        tabs = await _browser.list_tabs()
        if not tabs:
            return ToolResult.ok("No open tabs.")
        lines = [f"tab_id={t['tab_id']!r}  url={t['url']!r}  title={t['title']!r}"
                 for t in tabs]
        return ToolResult.ok("\n".join(lines))
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to list tabs: {e}")


@tool(
    name="browser_focus_tab",
    description=(
        "Switch the active browser tab to the one identified by tab_id "
        "(from browser_new_tab or browser_list_tabs). "
        "Subsequent browser actions will operate on this tab."
    ),
)
async def browser_focus_tab(tab_id: str, _browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        url = await _browser.focus_tab(tab_id)
        return ToolResult.ok(f"Focused tab {tab_id!r}. Current URL: {url}")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except ValueError as e:
        return ToolResult.error(str(e))
    except Exception as e:
        return ToolResult.error(f"Failed to focus tab {tab_id!r}: {e}")


@tool(
    name="browser_close_tab",
    description=(
        "Close the browser tab identified by tab_id (from browser_new_tab or browser_list_tabs). "
        "Cannot close the default tab; use browser_close for that."
    ),
)
async def browser_close_tab(tab_id: str, _browser=None) -> ToolResult:
    if _browser is None:
        return _no_browser()
    try:
        await _browser.close_tab(tab_id)
        return ToolResult.ok(f"Closed tab {tab_id!r}.")
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except ValueError as e:
        return ToolResult.error(str(e))
    except Exception as e:
        return ToolResult.error(f"Failed to close tab {tab_id!r}: {e}")


# ---------------------------------------------------------------------------
# Remote Chrome (CDP connect)
# ---------------------------------------------------------------------------

@tool(
    name="browser_connect_user_chrome",
    description=(
        "Connect to the user's Chrome browser via CDP remote debugging. "
        "This lets you control tabs where the user is already logged in (Gmail, GitHub, etc.) "
        "without re-authenticating. "
        "Chrome is launched automatically if not already running with remote debugging enabled. "
        "Pass the debugging URL, e.g. 'http://localhost:9222'. "
        "Returns a list of currently open tabs."
    ),
)
async def browser_connect_user_chrome(
    debugging_url: str = "http://localhost:9222",
    _browser=None,
) -> ToolResult:
    if _browser is None:
        return _no_browser()
    if not debugging_url.startswith(("http://", "https://")):
        return ToolResult.error(
            f"Invalid debugging URL (must start with http/https): {debugging_url}"
        )
    try:
        tabs = await _browser.connect_remote_chrome(debugging_url)
        if not tabs:
            return ToolResult.ok(
                f"Connected to Chrome at {debugging_url}. No open tabs found."
            )
        lines = [f"Connected to Chrome at {debugging_url}. Open tabs:"]
        for t in tabs:
            lines.append(f"  url={t['url']!r}  title={t['title']!r}")
        return ToolResult.ok("\n".join(lines))
    except ImportError:
        return ToolResult.error(_INSTALL_HINT)
    except Exception as e:
        return ToolResult.error(f"Failed to connect to Chrome at {debugging_url!r}: {e}")
