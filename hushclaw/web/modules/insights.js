/**
 * insights.js — independent Insights panel.
 */

import { tasksState, send } from "./state.js";
import { openConfirm } from "./modal.js";

const INSIGHT_PAGE_LIMIT = 30;

function formatInsightTime(raw) {
  const n = Number(raw || 0);
  if (!Number.isFinite(n) || n <= 0) return "";
  return new Date(n * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

function buildLoadMoreRow(label, onClick) {
  const wrap = document.createElement("div");
  wrap.className = "load-more-row insights-load-more";
  const btn = document.createElement("button");
  btn.className = "secondary load-more-btn";
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  wrap.appendChild(btn);
  return wrap;
}

export function refreshInsights(offset = 0) {
  send({ type: "list_insights", offset, limit: tasksState.insightLimit || INSIGHT_PAGE_LIMIT });
}

export function renderInsights(items, hasMore = false, offset = 0) {
  const append = Number(offset || 0) > 0;
  tasksState.insights = append ? [...tasksState.insights, ...items] : items;
  tasksState.insightOffset = Number(offset || 0) + items.length;
  tasksState.insightsHasMore = Boolean(hasMore);
  const el = document.getElementById("insights-list");
  if (!el) return;
  el.innerHTML = "";
  if (!tasksState.insights.length) {
    el.innerHTML = '<div class="insights-empty"><strong>No insights yet</strong><span>Save sharp principles, quotes, and methods worth reusing.</span></div>';
    return;
  }
  tasksState.insights.forEach(item => el.appendChild(buildInsightRow(item)));
  if (tasksState.insightsHasMore) {
    el.appendChild(buildLoadMoreRow("Load more insights", () => refreshInsights(tasksState.insightOffset)));
  }
}

export function buildInsightRow(item) {
  const row = document.createElement("div");
  row.className = "insight-row";
  row.dataset.id = item.note_id || "";

  const body = document.createElement("div");
  body.className = "insight-body";

  const text = document.createElement("div");
  text.className = "insight-text";
  text.textContent = item.body || item.title || "Insight";

  const meta = document.createElement("div");
  meta.className = "insight-meta";

  const typeBadge = document.createElement("span");
  typeBadge.className = "insight-badge insight-type";
  typeBadge.textContent = item.note_type === "interest" ? "interest" : "belief";
  meta.appendChild(typeBadge);

  const sourceBadge = document.createElement("span");
  const source = item.source_type === "curated" ? "curated" : "memory";
  sourceBadge.className = `insight-badge insight-source insight-source--${source}`;
  sourceBadge.textContent = source;
  meta.appendChild(sourceBadge);

  const dateText = formatInsightTime(item.created_at || item.created);
  if (dateText) {
    const date = document.createElement("span");
    date.className = "insight-date";
    date.textContent = dateText;
    meta.appendChild(date);
  }

  body.appendChild(text);
  body.appendChild(meta);

  const del = document.createElement("button");
  del.className = "insight-delete icon-btn secondary";
  del.textContent = "✕";
  del.title = "Delete insight";
  del.addEventListener("click", async () => {
    const confirmed = await openConfirm({
      title: "Delete insight",
      message: "Delete this insight?",
      confirmText: "Delete",
      cancelText: "Cancel",
      dangerConfirm: true,
    });
    if (!confirmed) return;
    send({ type: "delete_insight", note_id: item.note_id || "" });
  });

  row.appendChild(body);
  row.appendChild(del);
  return row;
}

export function onInsightCreated(item) {
  if (!item) return;
  tasksState.insights = [
    { ...item, source_type: item.source_type || "curated" },
    ...tasksState.insights.filter(existing => existing.note_id !== item.note_id),
  ];
  renderInsights(tasksState.insights, tasksState.insightsHasMore, 0);
  refreshInsights(0);
}

export function onInsightDeleted(noteId, ok) {
  if (!ok) return;
  tasksState.insights = tasksState.insights.filter(item => item.note_id !== noteId);
  renderInsights(tasksState.insights, tasksState.insightsHasMore, 0);
}

function submitInsight() {
  const textEl = document.getElementById("insight-text-input");
  const typeEl = document.getElementById("insight-type-input");
  const text = textEl?.value.trim() || "";
  if (!text) return;
  send({
    type: "create_insight",
    text,
    note_type: typeEl?.value === "interest" ? "interest" : "belief",
    tags: ["insight"],
  });
  if (textEl) textEl.value = "";
  document.getElementById("insight-add-row")?.classList.add("hidden");
  tasksState.addingInsight = false;
}

document.getElementById("btn-add-insight")?.addEventListener("click", () => {
  const row = document.getElementById("insight-add-row");
  if (!row) return;
  tasksState.addingInsight = true;
  row.classList.remove("hidden");
  document.getElementById("insight-text-input")?.focus();
});

document.getElementById("btn-insight-cancel")?.addEventListener("click", () => {
  document.getElementById("insight-add-row")?.classList.add("hidden");
  tasksState.addingInsight = false;
});

document.getElementById("btn-insight-submit")?.addEventListener("click", submitInsight);

document.getElementById("insight-text-input")?.addEventListener("keydown", (ev) => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") submitInsight();
  if (ev.key === "Escape") document.getElementById("btn-insight-cancel")?.click();
});
