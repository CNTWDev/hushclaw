"""Update execution orchestration."""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import sys
import tempfile
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

    async def launch_delegate(self, on_progress: ProgressCallback) -> dict:
        """Launch the installer as a fully detached process and return immediately.

        Writes a wrapper script that:
          1. Sleeps briefly so the server process can exit cleanly.
          2. Does ``git fetch origin`` to pull the latest commit.
          3. Extracts the freshest ``install.sh`` from FETCH_HEAD so bash never
             reads a stale or mid-update file on disk.
          4. Replaces itself (``exec``) with that script, running ``--update``.
             Falls back to the local copy when the network is unavailable.

        The wrapper is launched in a new process session (start_new_session=True)
        with all stdio closed, so it survives the parent dying and has no shared
        pipe that could produce a SIGPIPE or deadlock.

        The caller MUST exit the server process shortly after this returns so the
        delegate can take over.
        """
        cmd = self._pick_command()
        cmd_str = shlex.join(cmd)
        repo_root = Path(__file__).resolve().parents[2]
        log.info("launch_delegate: repo_root=%s command=%s", repo_root, cmd_str)

        try:
            await on_progress("prepare", "running", "Preparing update delegate…")

            if os.name == "nt":
                # Windows: PowerShell wrapper (git fetch + run latest install.ps1)
                repo_esc = str(repo_root).replace("'", "''")
                wrapper = (
                    f"Set-Location '{repo_esc}'\n"
                    "git fetch origin --quiet 2>$null\n"
                    "git show FETCH_HEAD:install.ps1 | Set-Content $env:TEMP\\hushclaw_install_latest.ps1 -ErrorAction SilentlyContinue\n"
                    "if (Test-Path $env:TEMP\\hushclaw_install_latest.ps1) {\n"
                    "  & $env:TEMP\\hushclaw_install_latest.ps1 -Update\n"
                    "} else {\n"
                    f"  {cmd_str}\n"
                    "}\n"
                )
                tmp = Path(tempfile.gettempdir()) / "hushclaw_update.ps1"
                tmp.write_text(wrapper, encoding="utf-8")
                proc = subprocess.Popen(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(tmp)],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Unix: fetch latest install.sh from git, exec into it.
                # Using exec replaces this wrapper process — no zombie, no wait.
                # Fallback to local copy when git fetch / git show fail (no network).
                repo_q = shlex.quote(str(repo_root))
                wrapper = (
                    "#!/bin/bash\n"
                    "# HushClaw detached update wrapper — auto-generated, safe to delete\n"
                    "sleep 2  # wait for the parent server process to exit fully\n"
                    f"cd {repo_q}\n"
                    "git fetch origin --quiet 2>/dev/null || true\n"
                    "LATEST=$(mktemp /tmp/hushclaw_install_XXXXXX.sh)\n"
                    "if git show FETCH_HEAD:install.sh > \"$LATEST\" 2>/dev/null; then\n"
                    "  chmod +x \"$LATEST\"\n"
                    "  exec bash \"$LATEST\" --update\n"
                    "else\n"
                    "  exec bash install.sh --update\n"
                    "fi\n"
                )
                tmp = Path(tempfile.gettempdir()) / "hushclaw_update.sh"
                tmp.write_text(wrapper, encoding="utf-8")
                tmp.chmod(0o700)
                proc = subprocess.Popen(
                    ["bash", str(tmp)],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )

            log.info("launch_delegate: detached wrapper started pid=%d path=%s", proc.pid, tmp)
            await on_progress(
                "install", "running",
                f"Update delegate launched (PID {proc.pid}). Server shutting down for restart…",
            )
            return {"ok": True, "error": "", "restart_required": True, "command": cmd_str}

        except Exception as exc:
            log.error("launch_delegate: failed to launch delegate: %s", exc)
            await on_progress("prepare", "error", f"Failed to launch update delegate: {exc}")
            return {"ok": False, "error": str(exc), "restart_required": False, "command": cmd_str}

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
