/**
 * briefing.js — Proactive workspace briefing and suggestion cards.
 */

import { state, els, send, escHtml, showToast } from "./state.js";

function _workspace() {
  return state.activeWorkspace || "";
}

function _dismissKey(id) {
  return `${_workspace()}:${id}`;
}

function _sourceLabel(source) {
  if (!source) return "";
  const title = source.title || source.id || source.type || "";
  return String(title).replace(/\s+/g, " ").trim();
}

function _visibleSuggestions(items = []) {
  return items.filter((item) => item?.id && !state.briefingDismissed.has(_dismissKey(item.id)));
}

function _insertPrompt(prompt) {
  const text = String(prompt || "").trim();
  if (!text || !els.input) return;
  els.input.value = text;
  els.input.focus();
  els.input.dispatchEvent(new Event("input", { bubbles: true }));
}

function _renderSuggestion(item) {
  const card = document.createElement("article");
  card.className = `brief-suggestion brief-suggestion--${String(item.type || "general").replace(/[^a-z0-9_-]/gi, "")}`;
  card.dataset.suggestionId = item.id || "";

  const sources = (item.sources || [])
    .map(_sourceLabel)
    .filter(Boolean)
    .slice(0, 2);

  card.innerHTML = `
    <div class="brief-suggestion-main">
      <div class="brief-suggestion-type">${escHtml(item.type || "suggestion")}</div>
      <div class="brief-suggestion-title">${escHtml(item.title || "Suggestion")}</div>
      <div class="brief-suggestion-body">${escHtml(item.body || "")}</div>
      ${sources.length ? `<div class="brief-sources">${sources.map(s => `<span>${escHtml(s)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="brief-suggestion-actions">
      <button class="brief-accept" type="button">${item.action === "create_todo" ? "Create Todo" : "Use"}</button>
      <button class="brief-dismiss" type="button" title="Dismiss">×</button>
    </div>
  `;

  card.querySelector(".brief-accept")?.addEventListener("click", () => {
    if (item.action === "chat_prompt") {
      _insertPrompt(item.prompt || item.body || "");
    }
    send({
      type: "accept_briefing_suggestion",
      suggestion_id: item.id,
      action: item.action || "chat_prompt",
      prompt: item.prompt || "",
      title: item.title || "",
      todo: item.todo || undefined,
    });
  });

  card.querySelector(".brief-dismiss")?.addEventListener("click", () => {
    state.briefingDismissed.add(_dismissKey(item.id));
    send({ type: "dismiss_briefing_suggestion", suggestion_id: item.id });
    renderWorkspaceBriefing(state.briefing);
  });

  return card;
}

export function requestWorkspaceBriefing() {
  send({ type: "get_workspace_briefing", workspace: _workspace() });
}

export function renderWorkspaceBriefing(data) {
  state.briefing = data || null;
  const root = els.workspaceBriefing;
  if (!root) return;
  if (!data) {
    root.classList.add("hidden");
    root.innerHTML = "";
    return;
  }

  const suggestions = _visibleSuggestions(data.suggestions || []);
  const focusItems = (data.focus_items || []).slice(0, 3);
  const risks = (data.risks || []).slice(0, 2);
  const workspace = data.workspace || "Default";
  const expanded = root.dataset.expanded === "1";
  const topSuggestion = suggestions[0];
  const riskCount = (data.risks || []).length;

  root.classList.remove("hidden");
  root.classList.toggle("is-expanded", expanded);
  root.innerHTML = `
    <div class="brief-head">
      <div class="brief-title-wrap">
        <div class="brief-kicker">Today Briefing</div>
        <h2>${escHtml(workspace)}</h2>
        <span class="brief-compact-meta">${suggestions.length} suggestion${suggestions.length === 1 ? "" : "s"} · ${riskCount} risk${riskCount === 1 ? "" : "s"}</span>
      </div>
      <div class="brief-head-actions">
        <button id="brief-toggle" type="button">${expanded ? "Collapse" : "Open"}</button>
        <button id="brief-refresh" type="button" title="Refresh briefing">↻</button>
      </div>
    </div>
    <div class="brief-compact-line">
      <span>${escHtml(data.summary || "")}</span>
      ${topSuggestion ? `<strong>${escHtml(topSuggestion.title || "")}</strong>` : ""}
    </div>
    <div class="brief-expanded-body">
      <div class="brief-scroll">
        <div class="brief-grid">
          <div class="brief-block">
            <div class="brief-block-title">Focus</div>
            <div class="brief-list">
              ${focusItems.length ? focusItems.map(item => `
                <div class="brief-line">
                  <strong>${escHtml(item.title || "Focus item")}</strong>
                  ${item.detail ? `<span>${escHtml(item.detail)}</span>` : ""}
                </div>
              `).join("") : `<div class="brief-empty">No active focus yet.</div>`}
            </div>
          </div>
          <div class="brief-block">
            <div class="brief-block-title">Watch</div>
            <div class="brief-list">
              ${risks.length ? risks.map(item => `
                <div class="brief-line brief-risk">
                  <strong>${escHtml(item.title || "Risk")}</strong>
                  ${item.detail ? `<span>${escHtml(item.detail)}</span>` : ""}
                </div>
              `).join("") : `<div class="brief-empty">No immediate risks.</div>`}
            </div>
          </div>
        </div>
        <div class="brief-suggestions-head">
          <span>Suggestions Inbox</span>
          <small>${suggestions.length} active</small>
        </div>
        <div class="brief-suggestions"></div>
      </div>
    </div>
  `;

  root.querySelector("#brief-toggle")?.addEventListener("click", () => {
    root.dataset.expanded = expanded ? "0" : "1";
    renderWorkspaceBriefing(state.briefing);
  });
  root.querySelector("#brief-refresh")?.addEventListener("click", requestWorkspaceBriefing);
  const list = root.querySelector(".brief-suggestions");
  if (!list) return;
  if (!suggestions.length) {
    list.innerHTML = `<div class="brief-empty brief-empty-wide">No active suggestions.</div>`;
    return;
  }
  suggestions.forEach(item => list.appendChild(_renderSuggestion(item)));
}

export function handleBriefingAccepted(data) {
  if (!data?.ok) {
    showToast("Briefing suggestion failed.", "err");
    return;
  }
  if (data.action === "create_todo") {
    showToast("Todo created from briefing.", "ok");
  } else {
    showToast("Suggestion ready in composer.", "ok");
  }
}
