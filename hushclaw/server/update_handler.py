"""Update check and execution handlers — extracted from server_impl.py.

Handles two WebSocket messages:
  check_update  — query GitHub for latest release
  run_update    — launch the upgrade delegate and exit
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger("hushclaw.server.update")


async def handle_check_update(ws, data: dict, gateway, update_service) -> None:
    """Check GitHub for latest release and return update status."""
    cfg = gateway.base_agent.config.update
    channel = (data.get("channel") or cfg.channel or "stable").strip().lower()
    include_prerelease = channel == "prerelease"
    force = bool(data.get("force", False))
    log.info("check_update: force=%s channel=%s", force, channel)
    result = await update_service.check_for_update(
        include_prerelease=include_prerelease,
        force=force,
    )
    log.info(
        "check_update: available=%s current=%s latest=%s cached=%s error=%r",
        result.get("update_available"), result.get("current_version"),
        result.get("latest_version"), result.get("cached"), result.get("error") or "",
    )
    await ws.send(json.dumps(result))
    if result.get("ok") and result.get("update_available"):
        await ws.send(json.dumps({
            "type": "update_available",
            "current_version": result.get("current_version", ""),
            "latest_version": result.get("latest_version", ""),
            "release_url": result.get("release_url", ""),
            "published_at": result.get("published_at", ""),
            "channel": result.get("channel", "stable"),
        }))


async def handle_run_update(
    ws,
    data: dict,
    gateway,
    update_executor,
    upgrade_lock: asyncio.Lock,
    upgrade_state: dict,
    running_sessions: set,
    connected_clients: set,
) -> None:
    """Execute update command and stream progress.

    *upgrade_state* is a mutable dict with a single key ``"in_progress": bool``
    so the caller can observe/reset this flag after the call returns.
    *running_sessions* and *connected_clients* are live sets owned by the server.
    """
    force_when_busy = bool(data.get("force_when_busy", False))
    log.info(
        "run_update: requested force_when_busy=%s running_sessions=%d",
        force_when_busy, len(running_sessions),
    )

    # Atomic check: hold the lock while we inspect session state and set
    # the in-progress flag so no concurrent call can slip through the gap.
    async with upgrade_lock:
        if upgrade_state["in_progress"]:
            log.info("run_update: blocked — upgrade already in progress")
            await ws.send(json.dumps({
                "type": "update_result",
                "ok": False,
                "error": "An upgrade is already in progress.",
                "restart_required": False,
                "command": "",
            }))
            return
        if running_sessions and not force_when_busy:
            log.info("run_update: blocked — %d active sessions", len(running_sessions))
            await ws.send(json.dumps({
                "type": "update_result",
                "ok": False,
                "error": (
                    f"Upgrade blocked: {len(running_sessions)} active sessions running. "
                    "Retry with force_when_busy=true to continue."
                ),
                "restart_required": False,
                "command": "",
            }))
            return
        upgrade_state["in_progress"] = True

    # Broadcast shutdown notice to all connected clients so UIs can show
    # a proper "server restarting for upgrade" message before the TCP drop.
    n_clients = len(connected_clients)
    log.info("run_update: broadcasting shutdown to %d clients", n_clients)
    shutdown_msg = json.dumps({"type": "server_shutdown", "reason": "upgrade"})
    dead: set = set()
    for client in list(connected_clients):
        try:
            await client.send(shutdown_msg)
        except Exception:
            dead.add(client)
    connected_clients -= dead

    async def emit(stage: str, status: str, message: str) -> None:
        await ws.send(json.dumps({
            "type": "update_progress",
            "stage": stage,
            "status": status,
            "message": message,
        }))

    await ws.send(json.dumps({
        "type": "update_progress",
        "stage": "start",
        "status": "running",
        "message": "Starting update…",
    }))

    # Launch a fully detached delegate script so the server can exit freely.
    # The delegate waits 2 s, then runs install.sh --update independently.
    # This avoids the self-kill deadlock where a child process tries to SIGTERM
    # its own parent while the parent is blocking on the child's stdout pipe.
    result = await update_executor.launch_delegate(emit)
    ok = bool(result.get("ok"))

    if ok:
        log.info("run_update: delegate launched — server will exit in 1 s to allow update")
    else:
        log.error("run_update: delegate launch failed: %s", result.get("error", ""))

    await ws.send(json.dumps({
        "type": "update_result",
        "ok": ok,
        "error": result.get("error", ""),
        "restart_required": ok,
        "command": result.get("command", ""),
    }))

    if ok:
        # Exit after a short delay so the WebSocket messages above can be
        # flushed to the client before the TCP connection drops.
        # os._exit bypasses atexit / __del__ cleanup intentionally — the
        # delegate will start a fresh server instance.
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        # upgrade_state["in_progress"] is intentionally NOT cleared; server is exiting.
    else:
        upgrade_state["in_progress"] = False
