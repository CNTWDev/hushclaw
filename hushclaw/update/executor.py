"""Update execution orchestration."""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path
from typing import Awaitable, Callable


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
            await on_progress("prepare", "running", f"Running: {shlex.join(cmd)}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                await on_progress("prepare", "error", f"Failed to launch updater: {exc}")
                return {
                    "ok": False,
                    "error": str(exc),
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }

            try:
                await asyncio.wait_for(
                    self._stream_output(proc, on_progress),
                    timeout=max(30, int(timeout_seconds)),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await on_progress("install", "error", "Update timed out; process killed.")
                return {
                    "ok": False,
                    "error": "Update timed out.",
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }

            code = proc.returncode
            if code == 0:
                await on_progress("done", "ok", "Update completed.")
                return {
                    "ok": True,
                    "error": "",
                    "restart_required": True,
                    "command": shlex.join(cmd),
                }
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
