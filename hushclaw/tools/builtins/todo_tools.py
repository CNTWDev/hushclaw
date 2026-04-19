"""Todo tools: add_todo, list_todos, complete_todo."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


@tool(
    name="add_todo",
    description=(
        "Add an item to the user's todo list — for tasks WITHOUT a specific scheduled time. "
        "If the user specifies a concrete time (e.g. '9:30 AM', '下午3点', 'tomorrow at 2pm'), "
        "use add_calendar_event instead. "
        "title (required): short description of the task. "
        "notes: optional details. "
        "priority: 0=normal, 1=high. "
        "due_date: ISO date string (YYYY-MM-DD) or null — date only, no time."
    ),
)
def add_todo(
    title: str,
    notes: str = "",
    priority: int = 0,
    due_date: str | None = None,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not title or not title.strip():
        return ToolResult.error("title cannot be empty — provide a short description of the task")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    due_at: int | None = None
    if due_date:
        try:
            from datetime import datetime
            due_at = int(datetime.fromisoformat(due_date).timestamp())
        except Exception:
            return ToolResult.error(f"Invalid due_date format: {due_date!r}. Use YYYY-MM-DD.")
    todo = _memory_store.add_todo(title, notes=notes, priority=priority, due_at=due_at)
    pri = "high" if priority else "normal"
    return ToolResult.ok(
        f"Todo added (id={todo['todo_id']}): {title!r} [priority={pri}]"
    )


@tool(
    name="list_todos",
    description="List the user's todos. status: 'pending' (default) | 'done' | 'all'.",
)
def list_todos(
    status: str = "pending",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    filter_status = None if status == "all" else status
    todos = _memory_store.list_todos(status=filter_status)
    if not todos:
        label = f"status={status}" if status != "all" else "any"
        return ToolResult.ok(f"No todos found ({label}).")
    lines = [f"Todos ({status}):"]
    for t in todos:
        pri = " [!]" if t["priority"] else ""
        due = f" due={t['due_at']}" if t["due_at"] else ""
        check = "☑" if t["status"] == "done" else "☐"
        lines.append(f"  {check} [{t['todo_id']}]{pri} {t['title']}{due}")
    return ToolResult.ok("\n".join(lines))


@tool(
    name="complete_todo",
    description=(
        "Mark a todo as done by its ID. "
        "todo_id (required): the ID returned by add_todo or list_todos."
    ),
)
def complete_todo(
    todo_id: str,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not todo_id or not todo_id.strip():
        return ToolResult.error("todo_id cannot be empty — provide the todo ID from list_todos")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    updated = _memory_store.update_todo(todo_id, status="done")
    if not updated:
        return ToolResult.error(f"Todo not found: {todo_id!r}")
    return ToolResult.ok(f"Todo marked as done: {updated['title']!r}")
