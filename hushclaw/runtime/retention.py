"""RetentionExecutor: enforces data retention policies from security_policies table.

Runs as a background asyncio task. On each cycle it:
  1. Reads retention policies from the security_policies table
  2. Deletes events and turns older than retention_days
  3. Prunes orphaned artifact rows whose referencing events were removed

This is a best-effort housekeeping task — a crash or skip never corrupts the
source of truth; it just means old data lingers until the next cycle.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore

log = get_logger("retention")

_DEFAULT_RETENTION_DAYS = 90
_RUN_INTERVAL = 6 * 3600  # seconds between cycles (6 hours)


class RetentionExecutor:
    """Background task that prunes expired events, turns, and artifacts."""

    def __init__(self, memory: "MemoryStore") -> None:
        self._memory = memory
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle (mirrors ProjectionWorker pattern for consistency)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background task (idempotent, safe to call from sync context)."""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        coro = self._run()
        try:
            self._task = asyncio.create_task(coro, name="retention-executor")
        except RuntimeError:
            coro.close()
            self._running = False

    async def stop(self) -> None:
        """Cancel the task and wait for it to finish."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self._enforce()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("retention cycle error: %s", exc)
            await asyncio.sleep(_RUN_INTERVAL)

    async def _enforce(self) -> None:
        """Run one retention cycle: read policies, delete expired rows."""
        policies = self._load_policies()
        if not policies:
            policies = [{"retention_days": _DEFAULT_RETENTION_DAYS}]

        now_ms = int(time.time() * 1000)
        now_sec = int(time.time())

        for policy in policies:
            days = int(policy.get("retention_days") or _DEFAULT_RETENTION_DAYS)
            cutoff_ms = now_ms - days * 86_400 * 1000
            cutoff_sec = now_sec - days * 86_400

            del_events = self._memory.conn.execute(
                "DELETE FROM events WHERE ts < ?", (cutoff_ms,)
            ).rowcount
            del_turns = self._memory.conn.execute(
                "DELETE FROM turns WHERE ts < ?", (cutoff_sec,)
            ).rowcount

            if del_events or del_turns:
                log.info(
                    "retention: pruned %d events + %d turns older than %d days",
                    del_events, del_turns, days,
                )

        # Prune artifact rows whose referencing events are gone
        del_artifacts = self._memory.conn.execute(
            "DELETE FROM artifacts "
            "WHERE created < ? "
            "AND artifact_id NOT IN ("
            "  SELECT artifact_id FROM events WHERE artifact_id != ''"
            ")",
            (now_sec - _DEFAULT_RETENTION_DAYS * 86_400,),
        ).rowcount
        if del_artifacts:
            log.info("retention: pruned %d orphaned artifact rows", del_artifacts)

        self._memory.conn.commit()

    def _load_policies(self) -> list[dict]:
        try:
            rows = self._memory.conn.execute(
                "SELECT tenant_id, retention_days FROM security_policies"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
