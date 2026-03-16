"""Auto-install a pip package when first needed by a connector or tool."""
from __future__ import annotations

import importlib
import subprocess
import sys

from hushclaw.util.logging import get_logger

log = get_logger("hushclaw.setup")


def ensure_package(import_name: str, pip_name: str | None = None) -> bool:
    """
    Return True if *import_name* is importable; auto-install via pip if not.

    Args:
        import_name: The Python module name used in ``import <import_name>``.
        pip_name:    The PyPI package name passed to ``pip install``.
                     Defaults to *import_name* when omitted.
    """
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        pass

    pkg = pip_name or import_name
    log.info("Package %r not found — installing %r automatically...", import_name, pkg)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            check=True,
            capture_output=True,
        )
        importlib.import_module(import_name)  # verify it's now importable
        log.info("Package %r installed successfully.", pkg)
        return True
    except Exception as exc:
        log.error("Auto-install of %r failed: %s", pkg, exc)
        return False
