"""Background scheduler: runs cron-triggered tasks via the Gateway."""
from __future__ import annotations

import asyncio
from datetime import datetime

from ghostclaw.util.logging import get_logger

log = get_logger("scheduler")


def _cron_matches(expr: str, dt: datetime) -> bool:
    """5-field cron: minute hour dom month dow (0=Monday, Python weekday() convention)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False

    def _match(field: str, val: int) -> bool:
        if field == "*":
            return True
        if "," in field:
            return val in [int(x) for x in field.split(",")]
        if "-" in field and "/" not in field:
            a, b = field.split("-", 1)
            return int(a) <= val <= int(b)
        if "/" in field:
            base, step = field.split("/", 1)
            start = 0 if base == "*" else int(base)
            return (val - start) % int(step) == 0
        return int(field) == val

    min_f, hr_f, dom_f, mon_f, dow_f = parts
    return (
        _match(min_f, dt.minute)
        and _match(hr_f, dt.hour)
        and _match(dom_f, dt.day)
        and _match(mon_f, dt.month)
        and _match(dow_f, dt.weekday())
    )


class Scheduler:
    """asyncio background task that fires due cron jobs every minute."""

    def __init__(self, memory_store, gateway) -> None:
        self._memory = memory_store
        self._gateway = gateway
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Scheduler started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Scheduler stopped")

    async def _loop(self) -> None:
        while True:
            now = datetime.now()
            wait = 60 - now.second
            await asyncio.sleep(wait)
            now = datetime.now()
            try:
                jobs = self._memory.get_due_scheduled_tasks(now)
            except Exception as exc:
                log.error("Scheduler: error fetching due tasks: %s", exc)
                continue
            for job in jobs:
                asyncio.create_task(self._run_job(job))
                try:
                    self._memory.update_scheduled_task_last_run(job["id"], now)
                except Exception as exc:
                    log.error("Scheduler: error updating last_run for %s: %s", job["id"], exc)

    async def _run_job(self, job: dict) -> None:
        agent = job.get("agent") or "default"
        session_id = f"sched_{job['id'][:8]}"
        prompt = job["prompt"]
        log.info("Scheduler: running job %s (agent=%s)", job["id"][:8], agent)
        try:
            await self._gateway.execute(agent, prompt, session_id=session_id)
        except Exception as exc:
            log.error("Scheduler: job %s failed: %s", job["id"][:8], exc)
