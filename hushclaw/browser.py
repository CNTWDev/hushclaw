"""BrowserSession: lazy-loaded Playwright browser wrapper for HushClaw agents."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, BrowserContext, Browser, Playwright, Locator

log = logging.getLogger(__name__)


class BrowserSession:
    """
    Lazy-loaded Playwright browser session.

    One instance per AgentLoop — preserves cookies/session state across tool calls.
    The browser is only launched on the first tool call (navigate, etc.).
    """

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
        storage_state_path: Path | None = None,
    ) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._storage_state_path = storage_state_path
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None
        # Multi-tab: extra tabs keyed by uuid tab_id
        self._pages: dict[str, "Page"] = {}
        self._active_tab_id: str = "default"
        # Accessibility snapshot: ref number → Locator (rebuilt each browser_snapshot call)
        self._snapshot_map: dict[int, "Locator"] = {}
        # For user handover (headed browser instance)
        self._headed_pw: "Playwright | None" = None
        self._headed_browser: "Browser | None" = None
        self._headed_ctx: "BrowserContext | None" = None

    async def _ensure_page(self) -> "Page":
        if self._page is None:
            from hushclaw.util.playwright_setup import ensure_playwright, get_playwright_install_hint
            if not ensure_playwright():
                raise RuntimeError(get_playwright_install_hint())
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)
            ctx_kwargs: dict = {
                # Realistic Chrome UA — headless Chromium's default UA is trivially
                # detectable by bot-protection systems.
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "viewport": {"width": 1366, "height": 768},
                "locale": "en-US",
            }
            if self._storage_state_path and self._storage_state_path.exists():
                ctx_kwargs["storage_state"] = str(self._storage_state_path)
            self._context = await self._browser.new_context(**ctx_kwargs)
            # playwright-stealth: patches 20+ fingerprint signals at the context
            # level (covers all tabs). Auto-installed if missing. Falls back
            # silently to the manual add_init_script below.
            from hushclaw.util.playwright_setup import ensure_playwright_stealth
            if ensure_playwright_stealth():
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(self._context)
                    log.debug("playwright-stealth applied to browser context")
                except Exception as e:
                    log.debug("playwright-stealth apply failed, using fallback: %s", e)
            # Manual stealth baseline — always runs on top of playwright-stealth
            # (or alone if stealth is unavailable). Covers the most common checks.
            await self._context.add_init_script("""
                // Remove the main bot-detection signal
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                // Headless Chrome has 0 plugins; real browsers have several
                Object.defineProperty(navigator, 'plugins', {
                    get: () => Object.assign([
                        { name: 'PDF Viewer',          filename: 'internal-pdf-viewer',           description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',           description: '' },
                    ], { __proto__: PluginArray.prototype })
                });
                // Language list matches the UA locale
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                // chrome.runtime is absent in headless — some checks look for it
                if (!window.chrome) {
                    window.chrome = { runtime: {}, app: { isInstalled: false } };
                }
                // Fix Notification permissions query behaviour in headless
                const _origPermsQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _origPermsQuery(params);
            """)
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self._timeout_ms)
            self._active_tab_id = "default"
        # Return the currently focused tab
        if self._active_tab_id != "default" and self._active_tab_id in self._pages:
            return self._pages[self._active_tab_id]
        return self._page

    # ------------------------------------------------------------------
    # Accessibility snapshot system
    # ------------------------------------------------------------------

    async def snapshot(self) -> str:
        """
        Scan the active page for visible interactive elements, assign stable
        numeric refs, and return a compact human-readable summary.

        Rebuilds _snapshot_map; refs are valid until the next snapshot() call.
        Token cost: ~10–30 chars per element vs. thousands for raw HTML.
        """
        page = await self._ensure_page()
        elements: list[dict] = await page.evaluate("""
            () => {
                const SELECTOR = [
                    'a[href]', 'button', 'input:not([type="hidden"])',
                    'select', 'textarea',
                    '[role="button"]', '[role="link"]', '[role="checkbox"]',
                    '[role="combobox"]', '[role="listbox"]', '[role="menuitem"]',
                    '[role="option"]', '[role="radio"]', '[role="switch"]',
                    '[role="tab"]', '[role="textbox"]',
                ].join(', ');
                const isVisible = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 &&
                           getComputedStyle(el).visibility !== 'hidden';
                };
                // querySelectorAll already returns unique elements; cap at 300
                const els = Array.from(document.querySelectorAll(SELECTOR))
                                 .filter(isVisible)
                                 .slice(0, 300);
                return els.map((el, i) => {
                    el.setAttribute('data-gcref', String(i + 1));
                    const label = (
                        el.getAttribute('aria-label') ||
                        el.getAttribute('placeholder') ||
                        el.getAttribute('title') ||
                        (el.innerText || '').trim().slice(0, 80) ||
                        el.value || ''
                    );
                    return {
                        ref: i + 1,
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || '',
                        role: el.getAttribute('role') || '',
                        label: label.slice(0, 80),
                    };
                });
            }
        """)

        self._snapshot_map = {}
        lines = ["Interactive elements:"]
        for el in elements:
            ref: int = el["ref"]
            tag: str = el["tag"]
            label: str = (el.get("label") or "").strip() or "(no label)"
            type_attr: str = el.get("type", "")
            role: str = el.get("role", "")

            if tag == "a":
                kind = "link"
            elif tag == "button" or role == "button":
                kind = "button"
            elif tag == "input":
                kind = f"input[type={type_attr}]" if type_attr else "input"
            elif tag in ("select", "textarea"):
                kind = tag
            else:
                kind = role or tag

            lines.append(f'[{ref}] {kind} "{label}"')
            self._snapshot_map[ref] = page.locator(f'[data-gcref="{ref}"]')

        if len(lines) == 1:
            lines.append("(no interactive elements found)")
        lines.append("")
        lines.append("Use ref numbers with browser_click_ref, browser_fill_ref")
        return "\n".join(lines)

    async def click_ref(self, ref: int) -> None:
        """Click the element identified by ref from the last snapshot() call."""
        if ref not in self._snapshot_map:
            raise ValueError(
                f"Ref [{ref}] not found. Call browser_snapshot first to get valid refs."
            )
        await self._snapshot_map[ref].click()

    async def fill_ref(self, ref: int, value: str) -> None:
        """Fill the input identified by ref from the last snapshot() call."""
        if ref not in self._snapshot_map:
            raise ValueError(
                f"Ref [{ref}] not found. Call browser_snapshot first to get valid refs."
            )
        await self._snapshot_map[ref].fill(value)

    # ------------------------------------------------------------------
    # Multi-tab support
    # ------------------------------------------------------------------

    async def new_tab(self, url: str = "", wait_until: str = "load") -> str:
        """
        Open a new browser tab. Optionally navigate to url.
        Returns a stable tab_id string for use with focus_tab / close_tab.
        The new tab becomes the active page.
        """
        import uuid
        await self._ensure_page()  # ensure _context exists
        tab_id = uuid.uuid4().hex[:8]
        new_page = await self._context.new_page()
        new_page.set_default_timeout(self._timeout_ms)
        self._pages[tab_id] = new_page
        if url:
            await new_page.goto(url, wait_until=wait_until)
        self._active_tab_id = tab_id
        self._snapshot_map.clear()
        return tab_id

    async def list_tabs(self) -> list[dict]:
        """Return all open tabs as a list of {tab_id, url, title} dicts."""
        tabs: list[dict] = []
        if self._page is not None:
            try:
                title = await self._page.title()
            except Exception as e:
                log.debug("could not get default page title: %s", e)
                title = ""
            tabs.append({"tab_id": "default", "url": self._page.url, "title": title})
        for tab_id, page in self._pages.items():
            try:
                title = await page.title()
            except Exception as e:
                log.debug("could not get tab %s title: %s", tab_id, e)
                title = ""
            tabs.append({"tab_id": tab_id, "url": page.url, "title": title})
        return tabs

    async def focus_tab(self, tab_id: str) -> str:
        """
        Switch the active page to the given tab_id.
        Returns the tab's current URL. Clears the snapshot map.
        """
        if tab_id == "default":
            if self._page is None:
                raise ValueError("No default page open yet.")
            self._active_tab_id = "default"
            self._snapshot_map.clear()
            return self._page.url
        if tab_id not in self._pages:
            raise ValueError(f"Unknown tab_id: {tab_id!r}")
        self._active_tab_id = tab_id
        self._snapshot_map.clear()
        return self._pages[tab_id].url

    async def close_tab(self, tab_id: str) -> None:
        """
        Close the tab identified by tab_id (must have been opened with new_tab).
        If this was the active tab, focus reverts to the default page.
        """
        if tab_id == "default":
            raise ValueError("Cannot close the default tab. Use browser_close instead.")
        if tab_id not in self._pages:
            raise ValueError(f"Unknown tab_id: {tab_id!r}")
        page = self._pages.pop(tab_id)
        await page.close()
        if self._active_tab_id == tab_id:
            self._active_tab_id = "default"
        self._snapshot_map.clear()

    # ------------------------------------------------------------------
    # Remote Chrome (CDP connect)
    # ------------------------------------------------------------------

    async def _do_connect_cdp(self, debugging_url: str) -> list[dict]:
        """Internal: connect to Chrome via CDP and return open tabs."""
        from hushclaw.util.playwright_setup import ensure_playwright, get_playwright_install_hint
        if not ensure_playwright():
            raise RuntimeError(get_playwright_install_hint())
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(debugging_url)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            pages = self._context.pages
            if pages:
                self._page = pages[0]
                self._page.set_default_timeout(self._timeout_ms)
            else:
                self._page = await self._context.new_page()
                self._page.set_default_timeout(self._timeout_ms)
        else:
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self._timeout_ms)
        self._active_tab_id = "default"
        self._pages.clear()
        self._snapshot_map.clear()

        tabs: list[dict] = []
        for page in self._context.pages:
            try:
                title = await page.title()
            except Exception as e:
                log.debug("could not get page title during CDP connect: %s", e)
                title = ""
            tabs.append({"url": page.url, "title": title})
        return tabs

    @staticmethod
    def _is_chrome_running() -> bool:
        """Return True if a Chrome/Chromium process is currently running."""
        import sys
        try:
            if sys.platform == "win32":
                import subprocess
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                    stderr=subprocess.DEVNULL,
                )
                return b"chrome.exe" in out
            else:
                import subprocess
                out = subprocess.check_output(
                    ["pgrep", "-f", "Google Chrome|Chromium|chromium-browser"],
                    stderr=subprocess.DEVNULL,
                )
                return bool(out.strip())
        except Exception:
            return False

    async def _launch_chrome_with_debugging(self, debugging_url: str) -> bool:
        """
        Find and launch Chrome with remote debugging enabled.

        Returns True if Chrome was found and launched, False if no binary found.
        Raises RuntimeError if Chrome is already running without a debug port
        (launching a second instance would lose all cookies/sessions).
        Raises TimeoutError if Chrome was launched but did not become ready in time.
        """
        import subprocess
        import sys
        import asyncio
        import urllib.request

        if self._is_chrome_running():
            # Chrome is running without a debug port — wait for the user to quit it
            # (up to 90 s) then relaunch automatically.  This avoids requiring the
            # user to send a second message after closing Chrome.
            import asyncio
            _WAIT_SECS = 90
            _POLL = 1.0
            for _ in range(int(_WAIT_SECS / _POLL)):
                await asyncio.sleep(_POLL)
                if not self._is_chrome_running():
                    break
            else:
                raise TimeoutError(
                    "Chrome is still running after 90 seconds.\n"
                    "Please quit Chrome completely (Cmd+Q on Mac) so it can be "
                    "relaunched with remote debugging enabled."
                )
            # Small grace period to let the OS release the profile lock
            await asyncio.sleep(1.0)

        chrome_bin = None

        if sys.platform == "darwin":
            import os
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
            for path in candidates:
                if os.path.isfile(path):
                    chrome_bin = path
                    break
        elif sys.platform == "win32":
            import os
            candidates = [
                os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                             "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Google", "Chrome", "Application", "chrome.exe"),
            ]
            for path in candidates:
                if os.path.isfile(path):
                    chrome_bin = path
                    break
        else:
            import shutil
            for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
                found = shutil.which(name)
                if found:
                    chrome_bin = found
                    break

        if chrome_bin is None:
            return False

        subprocess.Popen(
            [chrome_bin, "--remote-debugging-port=9222",
             "--no-first-run", "--no-default-browser-check"],
        )

        version_url = debugging_url.rstrip("/") + "/json/version"
        for _ in range(16):
            await asyncio.sleep(0.5)
            try:
                urllib.request.urlopen(version_url, timeout=1)
                return True
            except Exception:
                pass

        raise TimeoutError("Chrome did not become ready in time after launch.")

    async def connect_remote_chrome(self, debugging_url: str) -> list[dict]:
        """
        Connect to the user's Chrome via CDP remote debugging.

        Chrome is launched automatically with --remote-debugging-port=9222 if
        it is not already running with remote debugging enabled.

        Pass debugging_url as e.g. "http://localhost:9222".
        Returns a list of currently open tabs {url, title}.
        """
        try:
            return await self._do_connect_cdp(debugging_url)
        except (ConnectionRefusedError, OSError):
            # Chrome not yet running or not yet accepting connections — try launching it.
            pass
        except Exception as e:
            log.warning("CDP connect to %s failed unexpectedly: %s — attempting Chrome launch", debugging_url, e)
        launched = await self._launch_chrome_with_debugging(debugging_url)
        if not launched:
            raise RuntimeError(f"Could not connect to Chrome at {debugging_url} and launch also failed")
        return await self._do_connect_cdp(debugging_url)

    # ------------------------------------------------------------------
    # Core page operations (all operate on the active tab via _ensure_page)
    # ------------------------------------------------------------------

    async def navigate(self, url: str, wait_until: str = "load") -> str:
        """Navigate to URL, return final URL."""
        page = await self._ensure_page()
        await page.goto(url, wait_until=wait_until)
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
        """Close the browser, saving storage state if configured."""
        # Close extra tabs
        for page in list(self._pages.values()):
            try:
                await page.close()
            except Exception as exc:
                log.debug("error closing tab: %s", exc)
        self._pages.clear()
        self._snapshot_map.clear()
        self._active_tab_id = "default"

        if self._context is not None and self._storage_state_path is not None:
            try:
                self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                await self._context.storage_state(path=str(self._storage_state_path))
            except Exception as exc:
                log.debug("error saving storage state on close: %s", exc)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def open_for_user(self) -> str:
        """
        Save current storage state and open a visible (headed) browser window
        at the current URL so the user can handle sensitive operations directly.
        Returns the current URL.
        """
        import tempfile
        from playwright.async_api import async_playwright

        current_url = self._page.url if self._page else "about:blank"

        # Save current cookies to a temp file
        tmp_state = Path(tempfile.mktemp(suffix=".json"))
        if self._context is not None:
            try:
                await self._context.storage_state(path=str(tmp_state))
            except Exception as exc:
                log.debug("error saving storage state for user session: %s", exc)

        # Launch a headed browser
        self._headed_pw = await async_playwright().start()
        self._headed_browser = await self._headed_pw.chromium.launch(headless=False)
        ctx_kwargs: dict = {}
        if tmp_state.exists():
            ctx_kwargs["storage_state"] = str(tmp_state)
        self._headed_ctx = await self._headed_browser.new_context(**ctx_kwargs)
        headed_page = await self._headed_ctx.new_page()
        if current_url and current_url != "about:blank":
            try:
                await headed_page.goto(current_url)
            except Exception as exc:
                log.debug("error navigating user browser to %s: %s", current_url, exc)

        # Clean up temp file
        try:
            tmp_state.unlink(missing_ok=True)
        except Exception as exc:
            log.debug("error removing temp state file: %s", exc)

        return current_url

    async def close_user_session(self) -> None:
        """
        Close the headed browser, sync cookies back to the persistent storage
        state path, then reset the headless instance so the next tool call
        picks up the freshly synced cookies.
        """
        if self._headed_ctx is not None and self._storage_state_path is not None:
            try:
                self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                await self._headed_ctx.storage_state(path=str(self._storage_state_path))
            except Exception as exc:
                log.debug("error syncing storage state from user session: %s", exc)
        if self._headed_browser is not None:
            try:
                await self._headed_browser.close()
            except Exception as exc:
                log.debug("error closing headed browser: %s", exc)
            self._headed_browser = None
        if self._headed_pw is not None:
            try:
                await self._headed_pw.stop()
            except Exception as exc:
                log.debug("error stopping headed playwright: %s", exc)
            self._headed_pw = None
        self._headed_ctx = None

        # Reset the headless instance so next _ensure_page reloads with new cookies
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                log.debug("error closing headless browser during user session reset: %s", exc)
            self._browser = None
            self._context = None
            self._page = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:
                log.debug("error stopping headless playwright during user session reset: %s", exc)
            self._pw = None

    @property
    def current_url(self) -> str:
        if self._active_tab_id != "default" and self._active_tab_id in self._pages:
            return self._pages[self._active_tab_id].url
        return self._page.url if self._page else ""

    @property
    def is_open(self) -> bool:
        return self._page is not None
