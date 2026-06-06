/**
 * insights.js — independent Insights panel.
 */

import { tasksState, send, escHtml, showToast } from "./state.js";
import { openConfirm, openDialog, closeModal } from "./modal.js";

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
  send({
    type: "list_insights",
    view: tasksState.insightView || "curated",
    offset,
    limit: tasksState.insightLimit || INSIGHT_PAGE_LIMIT,
  });
}

export function renderInsights(items, hasMore = false, offset = 0, view = tasksState.insightView || "curated") {
  tasksState.insightView = view || "curated";
  const append = Number(offset || 0) > 0;
  tasksState.insights = append ? [...tasksState.insights, ...items] : items;
  tasksState.insightOffset = Number(offset || 0) + items.length;
  tasksState.insightsHasMore = Boolean(hasMore);
  const el = document.getElementById("insights-list");
  if (!el) return;
  el.innerHTML = "";
  if (!tasksState.insights.length) {
    el.innerHTML = tasksState.insightView === "suggested"
      ? '<div class="insights-empty"><strong>No suggested insights</strong><span>Auto-extracted candidates will appear here for review.</span></div>'
      : '<div class="insights-empty"><strong>No curated insights yet</strong><span>Save or promote sharp principles, quotes, and methods worth reusing.</span></div>';
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

  if (tasksState.insightView === "suggested") {
    const qualityBadge = document.createElement("span");
    const quality = item.quality === "delete" ? "cleanup" : "review";
    qualityBadge.className = `insight-badge insight-quality insight-quality--${quality}`;
    qualityBadge.textContent = quality;
    qualityBadge.title = item.quality_reason || "";
    meta.appendChild(qualityBadge);
  }

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
  if (tasksState.insightView === "suggested") {
    const promote = document.createElement("button");
    promote.className = "insight-promote secondary small";
    promote.textContent = "Promote";
    promote.title = "Promote to curated insights";
    promote.addEventListener("click", () => {
      send({ type: "apply_insight_cleanup", promote_ids: [item.note_id || ""] });
    });
    row.appendChild(promote);
  }
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

export function handleInsightCleanupPreview(data) {
  if (!data?.ok) {
    showToast("Failed to preview insight cleanup", "err");
    return;
  }
  tasksState.insightCleanupPreview = data;
  openInsightCleanupDialog(data);
}

export function handleInsightCleanupApplied(data) {
  if (!data?.ok) {
    showToast("Insight cleanup failed", "err");
    return;
  }
  showToast(
    `Insights cleaned: deleted ${data.deleted || 0}, kept ${data.kept || 0}, promoted ${data.promoted || 0}.`,
    "ok",
  );
  refreshInsights(0);
  closeModal();
}

function cleanupCandidateMarkup(item, action = "keep") {
  const id = escHtml(item.note_id || "");
  const text = escHtml(item.body || item.title || "Insight");
  const reason = escHtml(item.quality_reason || "Review before applying");
  const type = escHtml(item.note_type || "belief");
  return `
    <div class="insight-cleanup-row" data-note-id="${id}">
      <div class="insight-cleanup-main">
        <div class="insight-cleanup-text">${text}</div>
        <div class="insight-cleanup-meta"><span>${type}</span><span>${reason}</span></div>
      </div>
      <select class="insight-cleanup-action" data-note-id="${id}">
        <option value="keep" ${action === "keep" ? "selected" : ""}>Keep as Memory</option>
        <option value="promote" ${action === "promote" ? "selected" : ""}>Promote</option>
        <option value="delete" ${action === "delete" ? "selected" : ""}>Delete</option>
      </select>
    </div>`;
}

function openInsightCleanupDialog(data) {
  const autoDelete = data.auto_delete_candidates || [];
  const review = data.review_candidates || [];
  const autoPreview = autoDelete.map(item => cleanupCandidateMarkup(item, "delete")).join("");
  const reviewMarkup = review.map(item => cleanupCandidateMarkup(item, "keep")).join("");
  const html = `
    <div class="insight-cleanup-dialog">
      <div class="insight-cleanup-summary">
        <strong>${autoDelete.length}</strong> obvious low-value item${autoDelete.length === 1 ? "" : "s"} will be deleted.
        <strong>${review.length}</strong> item${review.length === 1 ? "" : "s"} need review.
      </div>
      ${autoDelete.length ? `
        <div class="insight-cleanup-section">
          <h4>Auto delete</h4>
          <p>These look like fragments or unfinished questions. They will be hard deleted when you apply.</p>
          <div class="insight-cleanup-list compact">${autoPreview}</div>
        </div>` : ""}
      ${review.length ? `
        <div class="insight-cleanup-section">
          <h4>Review</h4>
          <div class="insight-cleanup-list">${reviewMarkup}</div>
        </div>` : ""}
      ${data.has_more ? '<div class="insight-cleanup-more">More candidates exist. Run cleanup again after applying this batch.</div>' : ""}
    </div>`;
  openDialog({
    title: "Clean Insights",
    html,
    closeOnBackdrop: true,
    actions: [
      { label: "Cancel", secondary: true, onClick: () => closeModal() },
      { label: "Apply cleanup", danger: autoDelete.length > 0, onClick: applyCleanupFromDialog },
    ],
  });
}

function applyCleanupFromDialog() {
  const preview = tasksState.insightCleanupPreview || {};
  const auto_delete_ids = [];
  const delete_ids = [];
  const keep_ids = [];
  const promote_ids = [];
  document.querySelectorAll(".insight-cleanup-action").forEach((select) => {
    const noteId = select.dataset.noteId || "";
    if (!noteId) return;
    if (select.value === "delete") delete_ids.push(noteId);
    else if (select.value === "promote") promote_ids.push(noteId);
    else keep_ids.push(noteId);
  });
  send({ type: "apply_insight_cleanup", auto_delete_ids, delete_ids, keep_ids, promote_ids });
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

document.querySelectorAll(".insights-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.insightView || "curated";
    tasksState.insightView = view;
    document.querySelectorAll(".insights-tab").forEach((tab) => tab.classList.toggle("active", tab === btn));
    refreshInsights(0);
  });
});

document.getElementById("btn-clean-insights")?.addEventListener("click", () => {
  send({ type: "preview_insight_cleanup", limit: 50 });
});
