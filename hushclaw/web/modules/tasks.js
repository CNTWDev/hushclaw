/**
 * tasks.js — Tasks panel (todos + scheduled tasks) and its event listeners.
 */

import { state, tasksState, send, escHtml } from "./state.js";

// ── Todos ──────────────────────────────────────────────────────────────────

export function renderTodos(items) {
  tasksState.todos = items;
  const el = document.getElementById("todos-list");
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div class="tasks-empty">No todos yet.</div>';
    return;
  }
  const pending = items.filter(t => t.status !== "done");
  const done    = items.filter(t => t.status === "done");
  el.innerHTML = "";
  [...pending, ...done].forEach(todo => {
    el.appendChild(buildTodoRow(todo));
  });
}

export function buildTodoRow(todo) {
  const row = document.createElement("div");
  row.className = "todo-row" + (todo.status === "done" ? " done" : "") + (todo.priority ? " high-priority" : "");
  row.dataset.id = todo.todo_id;

  const check = document.createElement("button");
  check.className = "todo-check" + (todo.status === "done" ? " checked" : "");
  check.textContent = todo.status === "done" ? "☑" : "☐";
  check.title = todo.status === "done" ? "Mark as pending" : "Mark as done";
  check.addEventListener("click", () => {
    const newStatus = todo.status === "done" ? "pending" : "done";
    send({ type: "update_todo", todo_id: todo.todo_id, status: newStatus });
  });

  const title = document.createElement("span");
  title.className = "todo-title";
  title.textContent = todo.title;

  const meta = document.createElement("span");
  meta.className = "todo-meta";
  if (todo.priority) {
    const badge = document.createElement("span");
    badge.className = "priority-badge";
    badge.textContent = "!";
    meta.appendChild(badge);
  }
  if (todo.due_at) {
    const due = document.createElement("span");
    due.className = "todo-due";
    const d = new Date(todo.due_at * 1000);
    due.textContent = d.toLocaleDateString();
    meta.appendChild(due);
  }

  const del = document.createElement("button");
  del.className = "todo-del icon-btn secondary";
  del.textContent = "✕";
  del.title = "Delete todo";
  del.addEventListener("click", () => {
    send({ type: "delete_todo", todo_id: todo.todo_id });
    row.remove();
  });

  row.appendChild(check);
  row.appendChild(title);
  row.appendChild(meta);
  row.appendChild(del);
  return row;
}

export function onTodoCreated(item) {
  if (!item) return;
  tasksState.todos.push(item);
  renderTodos(tasksState.todos);
}

export function onTodoUpdated(item) {
  if (!item) return;
  const idx = tasksState.todos.findIndex(t => t.todo_id === item.todo_id);
  if (idx >= 0) tasksState.todos[idx] = item;
  else tasksState.todos.push(item);
  renderTodos(tasksState.todos);
}

export function onTodoDeleted(todo_id, ok) {
  if (ok) {
    tasksState.todos = tasksState.todos.filter(t => t.todo_id !== todo_id);
    renderTodos(tasksState.todos);
  }
}

function submitTodo() {
  const titleEl = document.getElementById("todo-title-input");
  const dueEl   = document.getElementById("todo-due-input");
  const title   = titleEl?.value.trim();
  if (!title) return;
  let due_at = null;
  if (dueEl?.value) {
    due_at = Math.floor(new Date(dueEl.value + "T00:00:00").getTime() / 1000);
  }
  send({
    type: "create_todo",
    title,
    priority: tasksState.todoPriority ? 1 : 0,
    due_at,
    tags: [],
  });
  if (titleEl) titleEl.value = "";
  if (dueEl)   dueEl.value = "";
  tasksState.todoPriority = false;
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-add-row")?.classList.add("hidden");
}

// ── Scheduled tasks ────────────────────────────────────────────────────────

export function renderScheduledTasks(tasks) {
  tasksState.scheduled = tasks;
  const el = document.getElementById("scheduled-list");
  if (!el) return;
  if (!tasks.length) {
    el.innerHTML = '<div class="tasks-empty">No scheduled tasks yet.</div>';
    return;
  }
  el.innerHTML = "";
  tasks.forEach(task => el.appendChild(buildSchedRow(task)));
}

export function buildSchedRow(task) {
  const row = document.createElement("div");
  row.className = "sched-row" + (task.enabled ? "" : " disabled");
  row.dataset.id = task.id;

  const icon = document.createElement("span");
  icon.className = "sched-icon";
  icon.textContent = task.run_once ? "⚡" : "⟳";
  icon.title = task.run_once ? "One-shot task" : "Recurring task";

  const info = document.createElement("div");
  info.className = "sched-info";
  const name = document.createElement("span");
  name.className = "sched-name";
  name.textContent = task.title || task.prompt.slice(0, 50);
  const cronSpan = document.createElement("span");
  cronSpan.className = "sched-cron";
  cronSpan.textContent = task.cron;
  info.appendChild(name);
  info.appendChild(cronSpan);

  name.style.cursor = "pointer";
  name.addEventListener("click", () => {
    const existing = row.querySelector(".sched-prompt-preview");
    if (existing) { existing.remove(); return; }
    const pre = document.createElement("div");
    pre.className = "sched-prompt-preview";
    pre.textContent = task.prompt;
    info.appendChild(pre);
  });

  const actions = document.createElement("div");
  actions.className = "sched-actions";

  const toggleBtn = document.createElement("button");
  toggleBtn.className = "secondary small";
  toggleBtn.textContent = task.enabled ? "⏸" : "▶";
  toggleBtn.title = task.enabled ? "Pause" : "Resume";
  toggleBtn.addEventListener("click", () => {
    send({ type: "toggle_scheduled_task", task_id: task.id, enabled: !task.enabled });
  });

  const runBtn = document.createElement("button");
  runBtn.className = "secondary small";
  runBtn.textContent = "▷";
  runBtn.title = "Run now";
  runBtn.addEventListener("click", () => {
    send({ type: "run_scheduled_task_now", task_id: task.id });
    runBtn.textContent = "…";
    setTimeout(() => { runBtn.textContent = "▷"; }, 2000);
  });

  const delBtn = document.createElement("button");
  delBtn.className = "danger small";
  delBtn.textContent = "✕";
  delBtn.title = "Delete";
  delBtn.addEventListener("click", () => {
    send({ type: "delete_scheduled_task", task_id: task.id });
    row.remove();
  });

  actions.appendChild(toggleBtn);
  actions.appendChild(runBtn);
  actions.appendChild(delBtn);

  row.appendChild(icon);
  row.appendChild(info);
  row.appendChild(actions);
  return row;
}

export function onTaskCreated(task) {
  if (!task) return;
  tasksState.scheduled.push(task);
  renderScheduledTasks(tasksState.scheduled);
}

export function onTaskToggled(task_id, enabled, ok) {
  if (!ok) return;
  const t = tasksState.scheduled.find(t => t.id === task_id);
  if (t) {
    t.enabled = enabled ? 1 : 0;
    renderScheduledTasks(tasksState.scheduled);
  }
}

export function populateSchedAgentSelect() {
  const sel = document.getElementById("sched-agent-select");
  if (!sel) return;
  sel.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "default";
  sel.appendChild(defaultOpt);
  state.agents.forEach(a => {
    if (a.name === "default") return;
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  });
}

export function buildCronFromSimple() {
  const freq = document.getElementById("sched-freq")?.value || "daily";
  const time = document.getElementById("sched-time")?.value || "09:00";
  const [h, m] = time.split(":").map(Number);
  if (freq === "hourly") return `${m} * * * *`;
  if (freq === "weekly") return `${m} ${h} * * 1`;
  return `${m} ${h} * * *`;
}

function submitSchedTask() {
  const title  = document.getElementById("sched-title-input")?.value.trim() || "";
  const prompt = document.getElementById("sched-prompt-input")?.value.trim() || "";
  if (!prompt) return;
  const modeEl = document.querySelector("input[name='sched-mode']:checked");
  const mode   = modeEl?.value || "simple";
  let cron;
  if (mode === "cron") {
    cron = document.getElementById("sched-cron-expr")?.value.trim() || "0 9 * * *";
  } else {
    cron = buildCronFromSimple();
  }
  const agent   = document.getElementById("sched-agent-select")?.value || "";
  const runOnce = document.getElementById("sched-run-once")?.checked || false;
  send({ type: "create_scheduled_task", title, cron, prompt, agent, run_once: runOnce });
  const titleEl  = document.getElementById("sched-title-input");
  const promptEl = document.getElementById("sched-prompt-input");
  if (titleEl)  titleEl.value = "";
  if (promptEl) promptEl.value = "";
  document.getElementById("sched-add-row")?.classList.add("hidden");
}

// ── Tasks event listeners ──────────────────────────────────────────────────

document.getElementById("btn-add-todo")?.addEventListener("click", () => {
  const row = document.getElementById("todo-add-row");
  if (!row) return;
  tasksState.addingTodo = true;
  tasksState.todoPriority = false;
  row.classList.remove("hidden");
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-title-input")?.focus();
});

document.getElementById("btn-todo-cancel")?.addEventListener("click", () => {
  document.getElementById("todo-add-row")?.classList.add("hidden");
  tasksState.addingTodo = false;
});

document.getElementById("todo-priority-btn")?.addEventListener("click", (e) => {
  tasksState.todoPriority = !tasksState.todoPriority;
  e.target.classList.toggle("active", tasksState.todoPriority);
});

document.getElementById("btn-todo-submit")?.addEventListener("click", submitTodo);

document.getElementById("todo-title-input")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") submitTodo();
  if (ev.key === "Escape") document.getElementById("btn-todo-cancel")?.click();
});

document.getElementById("btn-add-scheduled")?.addEventListener("click", () => {
  const row = document.getElementById("sched-add-row");
  if (!row) return;
  row.classList.remove("hidden");
  populateSchedAgentSelect();
  document.getElementById("sched-title-input")?.focus();
});

document.getElementById("btn-sched-cancel")?.addEventListener("click", () => {
  document.getElementById("sched-add-row")?.classList.add("hidden");
});

document.querySelectorAll("input[name='sched-mode']").forEach(radio => {
  radio.addEventListener("change", () => {
    const isCron = radio.value === "cron";
    document.getElementById("sched-simple-inputs")?.classList.toggle("hidden", isCron);
    document.getElementById("sched-cron-input")?.classList.toggle("hidden", !isCron);
  });
});

document.getElementById("btn-sched-submit")?.addEventListener("click", submitSchedTask);
