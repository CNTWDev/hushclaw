"""Auto-install Playwright when first needed."""
from __future__ import annotations

import subprocess
import sys

from ghostclaw.util.logging import get_logger

log = get_logger("browser.setup")


def ensure_playwright() -> bool:
    """Return True if playwright is available; auto-install if missing."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        pass
    log.info("Playwright not found — installing automatically...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
        )
        log.info("Playwright installed successfully.")
        return True
    except Exception as e:
        log.error("Playwright auto-install failed: %s", e)
        return False
