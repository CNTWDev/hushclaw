"""Update execution orchestration."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

# #region agent log
_DBG_LOG = Path("/Users/tuanwei/Desktop/Code Space/src/python/hushclaw/.cursor/debug-dc60c9.log")

def _dbg(msg: str, data: dict, hyp: str) -> None:
    try:
        entry = {"sessionId": "dc60c9", "timestamp": int(time.time() * 1000),
                 "location": "executor.py", "message": msg, "hypothesisId": hyp, "data": data}
        with open(_DBG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
# #endregion


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
            # #region agent log
            install_sh = Path(__file__).resolve().parents[2] / "install.sh"
            _dbg("pick_command", {"cmd": cmd, "install_sh_exists": install_sh.exists(),
                                  "install_sh_path": str(install_sh)}, "C")
            # #endregion
            await on_progress("prepare", "running", f"Running: {shlex.join(cmd)}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                # #region agent log
                _dbg("subprocess_launch_failed", {"error": str(exc)}, "C")
                # #endregion
                await on_progress("prepare", "error", f"Failed to launch updater: {exc}")
                return {
                    "ok": False,
                    "error": str(exc),
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }

            # #region agent log
            _dbg("subprocess_started", {"pid": proc.pid, "cmd": cmd}, "A_B_D")
            # #endregion

            try:
                await asyncio.wait_for(
                    self._stream_output(proc, on_progress),
                    timeout=max(30, int(timeout_seconds)),
                )
            except asyncio.TimeoutError:
                # #region agent log
                _dbg("stream_timeout", {"pid": proc.pid}, "D")
                # #endregion
                proc.kill()
                await on_progress("install", "error", "Update timed out; process killed.")
                return {
                    "ok": False,
                    "error": "Update timed out.",
                    "restart_required": False,
                    "command": shlex.join(cmd),
                }
            except Exception as exc:
                # #region agent log
                _dbg("stream_exception", {"error": str(exc), "type": type(exc).__name__, "pid": proc.pid}, "A_B_D")
                # #endregion

            code = proc.returncode
            # #region agent log
            _dbg("subprocess_exited", {"returncode": code, "pid": proc.pid}, "A_B_D")
            # #endregion
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
