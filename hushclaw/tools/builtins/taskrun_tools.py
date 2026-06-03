"""Lightweight TaskRun worker tools."""
from __future__ import annotations

import json

from hushclaw.tools.base import ToolResult, tool


@tool(
    name="create_work_task",
    description=(
        "Create a queued work task for later worker execution. This is for "
        "task lifecycle tracking, not recurring cron scheduling."
    ),
    mutating=True,
)
def create_work_task(
    title: str,
    spec: str = "",
    workspace: str = "",
    model_override: str = "",
    _memory_store=None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("create_work_task requires a memory store")
    title = (title or "").strip()
    if not title:
        return ToolResult.error("title is required")
    task = _memory_store.create_task(
        title,
        spec=spec or "",
        workspace=workspace or "",
        model_override=model_override or "",
    )
    return ToolResult.ok(json.dumps(task, ensure_ascii=False, indent=2))


@tool(name="list_work_tasks", description="List lightweight work tasks by status.", parallel_safe=True)
def list_work_tasks(status: str = "", limit: int = 50, _memory_store=None) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("list_work_tasks requires a memory store")
    tasks = _memory_store.list_tasks(status=status or None, limit=limit)
    return ToolResult.ok(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2))


@tool(name="claim_work_task", description="Claim a queued work task for a worker.", mutating=True)
def claim_work_task(
    task_id: str,
    worker_id: str = "agent",
    session_id: str = "",
    ttl_seconds: int = 900,
    _memory_store=None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("claim_work_task requires a memory store")
    run = _memory_store.claim_task(
        task_id,
        worker_id=worker_id or "agent",
        session_id=session_id or "",
        ttl_seconds=ttl_seconds,
    )
    if not run:
        return ToolResult.error(f"Task not claimable: {task_id}")
    return ToolResult.ok(json.dumps(run, ensure_ascii=False, indent=2))


@tool(name="complete_work_task", description="Mark a claimed work task run as succeeded.", mutating=True)
def complete_work_task(run_id: str, result: str = "", _memory_store=None) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("complete_work_task requires a memory store")
    ok = _memory_store.complete_task_run(run_id, result=result or "")
    return ToolResult.ok(f"Completed task run {run_id}") if ok else ToolResult.error(f"Task run not found: {run_id}")
