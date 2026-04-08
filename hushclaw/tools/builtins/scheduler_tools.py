"""Scheduler tools: schedule_task, list_scheduled_tasks, cancel_scheduled_task."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


@tool(
    name="schedule_task",
    description=(
        "Schedule a task to run automatically on a cron schedule. "
        "cron (required): 5-field cron expression 'minute hour day month weekday' (0=Monday for weekday). "
        "Examples: '0 8 * * *' = every day at 08:00, "
        "'0 9 * * 0' = every Monday at 09:00, "
        "'*/30 * * * *' = every 30 minutes. "
        "prompt (required): the instruction the agent will execute at that time."
    ),
)
def schedule_task(
    cron: str,
    prompt: str,
    agent: str = "",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not cron or not cron.strip():
        return ToolResult.error("cron cannot be empty — provide a 5-field cron expression, e.g. '0 8 * * *'")
    if not prompt or not prompt.strip():
        return ToolResult.error("prompt cannot be empty — provide the instruction the agent should execute")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    task_id = _memory_store.add_scheduled_task(cron, prompt, agent)
    return ToolResult.ok(
        f"Scheduled task created (id={task_id[:8]}). "
        f"Cron: '{cron}'. Prompt: {prompt!r}"
    )


@tool(
    name="list_scheduled_tasks",
    description="List all active scheduled tasks.",
)
def list_scheduled_tasks(
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    tasks = _memory_store.list_scheduled_tasks()
    if not tasks:
        return ToolResult.ok("No active scheduled tasks.")
    lines = ["Active scheduled tasks:"]
    for t in tasks:
        last = t["last_run"] or "never"
        lines.append(
            f"  - ID: {t['id'][:8]} | Cron: {t['cron']} | "
            f"Agent: {t['agent'] or 'default'} | Last run: {last}\n"
            f"    Prompt: {t['prompt']}"
        )
    return ToolResult.ok("\n".join(lines))


@tool(
    name="cancel_scheduled_task",
    description=(
        "Cancel (disable) a scheduled task by its ID. "
        "task_id (required): the ID returned by schedule_task (prefix match supported)."
    ),
)
def cancel_scheduled_task(
    task_id: str,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not task_id or not task_id.strip():
        return ToolResult.error("task_id cannot be empty — provide the task ID from list_scheduled_tasks")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    ok = _memory_store.cancel_scheduled_task(task_id)
    if ok:
        return ToolResult.ok(f"Scheduled task {task_id[:8]} cancelled.")
    return ToolResult.error(f"No active task found with ID starting '{task_id}'.")
