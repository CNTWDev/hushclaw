"""Playwright availability checks + best-effort auto-install."""
from __future__ import annotations

import importlib
import subprocess
import sys
from shutil import which

from hushclaw.util.logging import get_logger

log = get_logger("browser.setup")

_LAST_SETUP_ERROR: str = ""


def _set_last_error(msg: str) -> None:
    global _LAST_SETUP_ERROR
    _LAST_SETUP_ERROR = msg


def _can_import_playwright() -> bool:
    """True when playwright async API is importable in the current interpreter."""
    try:
        importlib.import_module("playwright.async_api")
        return True
    except Exception:
        return False


def get_playwright_install_hint() -> str:
    """Manual remediation hint with interpreter-specific commands."""
    py = sys.executable
    base = (
        "Playwright is unavailable in the current Python environment.\n"
        f"Python: {py}\n"
        "Run one of the following:\n"
        f"  {py} -m pip install playwright\n"
        f"  {py} -m playwright install chromium\n"
        "Or (recommended for this project):\n"
        f"  {py} -m pip install 'hushclaw[browser]'"
    )
    if _LAST_SETUP_ERROR:
        return f"{base}\nLast auto-install error: {_LAST_SETUP_ERROR}"
    return base


def ensure_playwright() -> bool:
    """Return True if Playwright is usable; auto-install if missing."""
    if _can_import_playwright():
        _set_last_error("")
        return True

    log.info("Playwright not found — installing automatically...")
    py = sys.executable
    _set_last_error("")

    pip_cmds: list[list[str]] = [
        [py, "-m", "pip", "install", "playwright"],
    ]
    for cmd in (["pip3", "install", "playwright"], ["pip", "install", "playwright"]):
        if which(cmd[0]):
            pip_cmds.append(cmd)

    last_install_error = ""
    for cmd in pip_cmds:
        try:
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=240,
            )
            out = (proc.stdout or "")[-400:]
            err = (proc.stderr or "")[-400:]
            log.info("Playwright install command succeeded: %s", " ".join(cmd))
            if out.strip():
                log.debug("Playwright pip stdout tail: %s", out)
            if err.strip():
                log.debug("Playwright pip stderr tail: %s", err)
            break
        except Exception as e:
            last_install_error = f"{' '.join(cmd)} -> {e}"
            log.warning("Playwright install command failed: %s", last_install_error)
    else:
        _set_last_error(last_install_error or "pip install command not available")
        return False

    browser_cmds: list[list[str]] = [
        [py, "-m", "playwright", "install", "chromium"],
    ]
    if which("playwright"):
        browser_cmds.append(["playwright", "install", "chromium"])

    last_browser_error = ""
    for cmd in browser_cmds:
        try:
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (proc.stdout or "")[-400:]
            err = (proc.stderr or "")[-400:]
            log.info("Playwright browser install succeeded: %s", " ".join(cmd))
            if out.strip():
                log.debug("Playwright install-browser stdout tail: %s", out)
            if err.strip():
                log.debug("Playwright install-browser stderr tail: %s", err)
            break
        except Exception as e:
            last_browser_error = f"{' '.join(cmd)} -> {e}"
            log.warning("Playwright browser install failed: %s", last_browser_error)
    else:
        _set_last_error(last_browser_error or "playwright install chromium failed")
        return False

    if _can_import_playwright():
        _set_last_error("")
        log.info("Playwright installed successfully.")
        return True

    _set_last_error("Install commands completed but playwright.async_api is still not importable.")
    log.error("Playwright appears installed but import still fails (python=%s).", py)
    return False
