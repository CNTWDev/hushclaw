"""BrowserSession: lazy-loaded Playwright browser wrapper for GhostClaw agents."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, BrowserContext, Browser, Playwright


class BrowserSession:
    """
    Lazy-loaded Playwright browser session.

    One instance per AgentLoop — preserves cookies/session state across tool calls.
    The browser is only launched on the first tool call (navigate, etc.).
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    async def _ensure_page(self) -> "Page":
        if self._page is None:
            from ghostclaw.util.playwright_setup import ensure_playwright
            if not ensure_playwright():
                raise RuntimeError(
                    "Playwright could not be installed automatically. "
                    "Run manually: pip install playwright && playwright install chromium"
                )
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self._timeout_ms)
        return self._page

    async def navigate(self, url: str) -> str:
        """Navigate to URL, wait for network idle, return final URL."""
        page = await self._ensure_page()
        await page.goto(url, wait_until="networkidle")
        return page.url

    async def content(self, selector: str = "body", as_text: bool = True) -> str:
        """Return rendered content of the page (or a CSS selector element)."""
        page = await self._ensure_page()
        if selector and selector != "body":
            el = await page.query_selector(selector)
            if el is None:
                return f"[Element not found: {selector}]"
            if as_text:
                return (await el.inner_text()) or ""
            return (await el.inner_html()) or ""
        if as_text:
            return await page.inner_text("body")
        return await page.content()

    async def click(self, selector: str) -> None:
        """Click an element identified by a CSS selector."""
        page = await self._ensure_page()
        await page.click(selector)

    async def fill(self, selector: str, value: str) -> None:
        """Fill an input element with a value."""
        page = await self._ensure_page()
        await page.fill(selector, value)

    async def screenshot(self, selector: str = "", save_dir: Path | None = None,
                         stem: str = "screenshot") -> str:
        """Take a screenshot, save to save_dir, return the saved file path."""
        page = await self._ensure_page()
        if save_dir is None:
            import tempfile
            save_dir = Path(tempfile.gettempdir())
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{stem}.png"
        if selector:
            el = await page.query_selector(selector)
            if el:
                await el.screenshot(path=str(path))
            else:
                await page.screenshot(path=str(path), full_page=True)
        else:
            await page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def evaluate(self, js: str) -> str:
        """Execute JavaScript in the page context and return the result as a string."""
        page = await self._ensure_page()
        result = await page.evaluate(js)
        return str(result) if result is not None else ""

    async def close(self) -> None:
        """Close the browser and release all resources."""
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    @property
    def is_open(self) -> bool:
        return self._page is not None
