"""Update execution orchestration."""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("hushclaw.update")

ProgressCallback = Callable[[str, str, str], Awaitable[None]]


class UpdateExecutor:
    """Runs upgrade commands with concurrency control and streamed logs."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def run_update(
        self,
        on_progress: ProgressCallback,
        timeout_seconds: int = 900,
    ) -> dict:
        if self._lock.locked():
            return {
                "ok": False,
                "error": "Another upgrade task is already running.",
                "restart_required": False,
                "command": "",
            }

        async with self._lock:
            cmd = self._pick_command()
            log.info("run_update: command=%s", shlex.join(cmd))
            await on_progress("prepare", "running", f"Running: {shlex.join(cmd)}")
            t0 = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                log.error("run_update: failed to launch subprocess: %s", exc)
                await on_progress("prepare", "error", f"Failed to launch updater: {exc}")
                return {
                    "ok": False,
                    "error": str(exc),
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }

            log.info("run_update: subprocess started pid=%s", proc.pid)

            try:
                await asyncio.wait_for(
                    self._stream_output(proc, on_progress),
                    timeout=max(30, int(timeout_seconds)),
                )
            except asyncio.TimeoutError:
                proc.kill()
                log.warning("run_update: timed out after %ss pid=%s — killed", timeout_seconds, proc.pid)
                await on_progress("install", "error", "Update timed out; process killed.")
                return {
                    "ok": False,
                    "error": "Update timed out.",
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }
            except Exception as exc:
                log.error("run_update: stream error pid=%s: %s", proc.pid, exc)

            code = proc.returncode
            elapsed = time.monotonic() - t0
            if code == 0:
                log.info("run_update: subprocess exited code=0 elapsed=%.1fs", elapsed)
                await on_progress("done", "ok", "Update completed.")
                return {
                    "ok": True,
                    "error": "",
                    "restart_required": True,
                    "command": shlex.join(cmd),
                }
            log.error("run_update: subprocess exited code=%s elapsed=%.1fs", code, elapsed)
            await on_progress("done", "error", f"Update command failed with exit code {code}.")
            return {
                "ok": False,
                "error": f"Updater exited with code {code}.",
                "restart_required": False,
                "command": shlex.join(cmd),
            }

    async def _stream_output(self, proc, on_progress: ProgressCallback) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                log.debug("run_update [pid=%s]: %s", proc.pid, line)
                await on_progress("install", "running", line[:1000])
        await proc.wait()

    def _pick_command(self) -> list[str]:
        repo_root = Path(__file__).resolve().parents[2]
        install_sh = repo_root / "install.sh"
        install_ps1 = repo_root / "install.ps1"

        if os.name == "nt" and install_ps1.exists():
            return [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(install_ps1),
                "-Update",
            ]
        if install_sh.exists():
            return ["bash", str(install_sh), "--update"]
        # Fallback: pip upgrade in current environment.
        return [sys.executable, "-m", "pip", "install", "-U", "hushclaw[server]"]
