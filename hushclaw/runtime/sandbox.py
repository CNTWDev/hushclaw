"""SandboxManager: owns browser sandbox lifecycle independently from AgentLoop.

Extracted from AgentLoop.__init__ / aclose() / _ensure_cdp() so that:
- Browser sessions are properly closed when AgentPool GCs stale loops (fixes DEBT-5)
- The sandbox abstraction can be extended to non-browser sandboxes (containers, etc.)
- AgentLoop only holds a reference, not direct ownership of the BrowserSession
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path
    from hushclaw.browser import BrowserSession
    from hushclaw.config.schema import BrowserConfig

log = get_logger("sandbox")


class SandboxManager:
    """Manages a browser sandbox: creation, CDP auto-connect, and cleanup."""

    def __init__(self, config: "BrowserConfig", data_dir: "Path | None" = None) -> None:
        from hushclaw.browser import BrowserSession

        storage_state_path = None
        if config.persist_cookies and data_dir is not None:
            storage_state_path = data_dir / "browser" / "cookies.json"

        self._session: BrowserSession = BrowserSession(
            headless=config.headless,
            timeout_ms=config.timeout * 1000,
            storage_state_path=storage_state_path,
        )
        self._cdp_url: str = config.remote_debugging_url if config.enabled else ""
        self._cdp_connected: bool = False
        self._closed: bool = False

    @property
    def session(self) -> "BrowserSession":
        return self._session

    async def ensure_cdp(self) -> None:
        """Connect to user Chrome via CDP on first call (if configured). One-shot."""
        if not self._cdp_url or self._cdp_connected or self._closed:
            return
        self._cdp_connected = True  # don't retry on failure
        try:
            tabs = await self._session.connect_remote_chrome(self._cdp_url)
            log.info("CDP auto-connected to %s — %d tab(s) open", self._cdp_url, len(tabs))
        except Exception as exc:
            log.warning("CDP auto-connect to %s failed: %s", self._cdp_url, exc)

    async def close(self) -> None:
        """Release browser resources. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._session.close()
        except Exception as exc:
            log.warning("sandbox close error: %s", exc)
