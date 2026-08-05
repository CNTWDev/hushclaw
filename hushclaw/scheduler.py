"""Background scheduler: runs cron-triggered tasks via the Gateway."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any

from hushclaw.util.logging import get_logger
from hushclaw.os_api import AgentOSService
from hushclaw.os_contracts import AgentOSMessageRequest

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

    def __init__(self, memory_store, gateway, on_task_event=None) -> None:
        self._memory = memory_store
        self._gateway = gateway
        self._os = getattr(gateway, "_os_api", None)
        if not isinstance(self._os, AgentOSService):
            raise RuntimeError("Scheduler requires an AgentOSService-bound gateway")
        self._on_task_event = on_task_event
        self._task: asyncio.Task | None = None
        self._work_task: asyncio.Task | None = None
        self._work_running: set[str] = set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        cfg = getattr(getattr(self._gateway, "_base_agent", None), "config", None)
        gateway_cfg = getattr(cfg, "gateway", None)
        if getattr(gateway_cfg, "work_task_worker_enabled", False):
            self._work_task = asyncio.create_task(self._work_loop())
        log.info("Scheduler started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._work_task:
            self._work_task.cancel()
            try:
                await self._work_task
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
                    if job.get("run_once"):
                        self._memory.disable_run_once_task(job["id"])
                        log.info("Scheduler: run_once job %s disabled after first run", job["id"][:8])
                except Exception as exc:
                    log.error("Scheduler: error updating last_run for %s: %s", job["id"], exc)

    async def _work_loop(self) -> None:
        cfg = getattr(getattr(self._gateway, "_base_agent", None), "config", None)
        gateway_cfg = getattr(cfg, "gateway", None)
        interval = max(5, int(getattr(gateway_cfg, "work_task_worker_interval_seconds", 30) or 30))
        max_concurrent = max(1, int(getattr(gateway_cfg, "work_task_worker_max_concurrent", 1) or 1))
        while True:
            await asyncio.sleep(interval)
            try:
                self._memory.mark_stale_task_runs()
                capacity = max_concurrent - len(self._work_running)
                if capacity <= 0:
                    continue
                candidates = self._memory.list_tasks(status="queued", limit=capacity)
                if len(candidates) < capacity:
                    candidates.extend(self._memory.list_tasks(status="stale", limit=capacity - len(candidates)))
                for task in candidates[:capacity]:
                    task_id = task.get("task_id")
                    if not task_id or not task.get("runnable", True) or task_id in self._work_running:
                        continue
                    self._work_running.add(task_id)
                    asyncio.create_task(self._run_work_task_worker(task_id))
            except Exception as exc:
                log.error("Scheduler: work task worker loop failed: %s", exc)

    async def _run_work_task_worker(self, task_id: str) -> None:
        try:
            task = self._memory.get_task(task_id) or {}
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            await self.run_work_task_now(
                task_id,
                agent=str(metadata.get("origin_agent") or "default"),
                worker_id="scheduler",
            )
        finally:
            self._work_running.discard(task_id)

    async def _run_job(self, job: dict) -> None:
        agent = job.get("agent") or "default"
        mode = getattr(self._gateway._base_agent.config.gateway, "scheduled_session_mode", "job")
        if mode == "run":
            run_tag = datetime.now().strftime("%Y%m%d%H%M%S")
            session_id = f"sched_{job['id'][:8]}_{run_tag}"
        else:
            session_id = f"sched_{job['id'][:8]}"
        prompt = job["prompt"]
        log.info("Scheduler: running job %s (agent=%s, session_mode=%s)", job["id"][:8], agent, mode)
        try:
            await self._os.execute_message(AgentOSMessageRequest(
                agent=agent,
                text=prompt,
                session_id=session_id,
                trigger_type="scheduled",
                source_channel="scheduler",
                principal_id=f"scheduler:{job['id']}",
                auth_context={"scheduled_task_id": job["id"]},
            ))
        except Exception as exc:
            log.error("Scheduler: job %s failed: %s", job["id"][:8], exc)

    async def run_work_task_now(
        self,
        task_id: str,
        *,
        agent: str = "default",
        worker_id: str = "scheduler",
        on_started=None,
    ) -> dict[str, Any]:
        """Claim and execute a lightweight work task through the existing gateway."""
        task = self._memory.get_task(task_id)
        if not task:
            return {"ok": False, "error": f"Task not found: {task_id}", "task_id": task_id}
        run = self._memory.claim_task(
            task_id,
            worker_id=worker_id,
            session_id=f"work_{task_id}",
            ttl_seconds=3600,
        )
        if not run:
            return {"ok": False, "error": f"Task not claimable: {task_id}", "task_id": task_id}
        if on_started is not None:
            await on_started({"task_id": task_id, "run": run, "session_id": run.get("session_id") or f"work_{task_id}"})
        await self._emit_task_event("started", task=task, run=run)
        prompt = task.get("spec") or task.get("title") or task_id
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        contract = metadata.get("completion_contract") if isinstance(metadata.get("completion_contract"), dict) else {}
        criteria = [str(item).strip() for item in contract.get("criteria", []) if str(item).strip()]
        if criteria:
            prompt += "\n\nDone when:\n" + "\n".join(f"- {item}" for item in criteria)
        lease_token = str(run.get("lease_token") or "")
        evidence: list[dict[str, Any]] = []
        heartbeat = asyncio.create_task(
            self._heartbeat_run(run["run_id"], lease_token, ttl_seconds=3600),
            name=f"work-task-heartbeat:{task_id[:12]}",
        )
        try:
            result = ""
            async for event in self._os.stream_message(AgentOSMessageRequest(
                agent=agent or "default",
                text=prompt,
                session_id=run.get("session_id") or f"work_{task_id}",
                workspace=str(task.get("workspace") or ""),
                model_override=str(task.get("model_override") or ""),
                trigger_type="scheduled",
                source_channel="scheduler:work_task",
                principal_id=f"scheduler:work:{task_id}",
                auth_context={
                    "task_id": task_id,
                    "task_run_id": run["run_id"],
                    "worker_id": worker_id,
                    "lease_token": lease_token,
                },
            )):
                event_type = str(event.get("type") or "")
                if event_type == "chunk":
                    result += str(event.get("text") or "")
                elif event_type == "done":
                    result = str(event.get("text") or result)
                elif event_type == "tool_result" and not event.get("is_error"):
                    evidence.append({
                        "kind": "tool",
                        "tool": str(event.get("tool") or ""),
                        "summary": str(event.get("result") or "")[:500],
                        "is_error": False,
                    })
                    for artifact in event.get("artifacts") or []:
                        if isinstance(artifact, dict):
                            evidence.append({
                                "kind": "artifact",
                                "tool": str(event.get("tool") or ""),
                                "title": str(artifact.get("title") or artifact.get("filename") or "Artifact"),
                                "url": str(artifact.get("url") or ""),
                                "artifact_id": str(artifact.get("artifact_id") or artifact.get("file_id") or ""),
                            })
            completed = self._memory.complete_task_run(
                run["run_id"], result=result or "", evidence=evidence, lease_token=lease_token
            )
            if not completed:
                rejected = self._memory.get_task_run(run["run_id"]) or run
                error = str(rejected.get("error") or "Task completion was rejected")
                await self._emit_task_event(
                    "failed",
                    task=self._memory.get_task(task_id) or task,
                    run=rejected,
                    error=error,
                )
                return {"ok": False, "task_id": task_id, "run_id": run["run_id"], "error": error}
            await self._emit_task_event(
                "completed",
                task=self._memory.get_task(task_id) or task,
                run=self._memory.get_task_run(run["run_id"]) or run,
                result=result or "",
            )
            return {"ok": True, "task_id": task_id, "run_id": run["run_id"], "result": result}
        except Exception as exc:
            self._memory.fail_task_run(
                run["run_id"], str(exc), evidence=evidence, lease_token=lease_token
            )
            await self._emit_task_event(
                "failed",
                task=self._memory.get_task(task_id) or task,
                run=self._memory.get_task_run(run["run_id"]) or run,
                error=str(exc),
            )
            log.error("Scheduler: work task %s failed: %s", task_id, exc)
            return {"ok": False, "task_id": task_id, "run_id": run["run_id"], "error": str(exc)}
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat_run(self, run_id: str, lease_token: str, ttl_seconds: int) -> None:
        """Renew a run lease while the provider/tool loop is still active."""
        interval = max(5, min(60, int(ttl_seconds / 3)))
        while True:
            await asyncio.sleep(interval)
            if not self._memory.heartbeat_task_run(run_id, lease_token, ttl_seconds=ttl_seconds):
                return

    async def _emit_task_event(self, state: str, *, task: dict, run: dict, result: str = "", error: str = "") -> None:
        if self._on_task_event is None:
            return
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        event = {
            "type": f"background_job_{state}",
            "task_id": str(task.get("task_id") or ""),
            "run_id": str(run.get("run_id") or ""),
            "session_id": str(metadata.get("origin_session_id") or ""),
            "agent": str(metadata.get("origin_agent") or "default"),
            "completion_mode": str(metadata.get("completion_mode") or "notify"),
            "status": state,
            "title": str(task.get("title") or "Background task"),
            "result": str(result or ""),
            "error": str(error or ""),
        }
        try:
            maybe = self._on_task_event(event)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception as exc:
            log.warning("background task event callback failed: task=%s error=%s", event["task_id"], exc)
