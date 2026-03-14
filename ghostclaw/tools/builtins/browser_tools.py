"""Browser tools: Playwright-powered browser automation for GhostClaw agents."""
from __future__ import annotations

import time
from pathlib import Path

from ghostclaw.tools.base import tool, ToolResult

_INSTALL_HINT = (
    "Playwright is not installed. "
    "Run: pip install ghostclaw[browser] && playwright install chromium"
)


def _no_browser() -> ToolResult:
    return ToolResult.error(
        "Browser session is not available. "
        "Ensure Playwright is installed: pip install ghostclaw[browser] && playwright install chromium"
    )


@tool(
    name="browser_navigate",
    description="Open a URL in the browser and wait for the page to fully load. "
                "Returns the final URL (after any redirects).",
)
async def browser_navigate(url: str, _browser=None) -> ToolResult:
    """Navigate to a URL and wait for network idle."""
    if _browser is None:
        return _no_browser()
    if not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
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
            save_dir = Path(tempfile.gettempdir()) / "ghostclaw_screenshots"

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
