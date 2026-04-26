/**
 * chat/tools.js — Tool labels, dev mode, tool-line bubbles, and tool-round blocks.
 *
 * Extracted from chat.js to keep individual modules under ~400 lines.
 * No imports from ../chat.js — avoids circular dependency.
 */

import { state, els, escHtml, prettyJson } from "../state.js";
import { renderMarkdown } from "../markdown.js";

// ── Private scroll/thinking helpers (identical to chat.js, inlined to avoid circularity) ──
function _scrollToBottom() { els.messages.scrollTop = els.messages.scrollHeight; }
function _pinThinkingMsgToBottom() {
  if (state._thinkingEl) els.messages.appendChild(state._thinkingEl);
}

// ── Developer mode ─────────────────────────────────────────────────────────
// When dev mode is off (default) tool lines show friendly Chinese labels.
// When on, they show raw tool names and result previews for debugging.
export function isDevMode() {
  try { return localStorage.getItem("hushclaw.dev.mode") === "1"; } catch { return false; }
}
export function setDevMode(on) {
  try { localStorage.setItem("hushclaw.dev.mode", on ? "1" : "0"); } catch { /* ignore */ }
}

// ── Friendly tool label map ────────────────────────────────────────────────
const TOOL_LABELS = {
  // Memory
  recall:                    { icon: "💭", running: "Searching memory…",        done: "Memory retrieved",       error: "Memory search failed" },
  remember:                  { icon: "📝", running: "Saving to memory…",        done: "Saved to memory",        error: "Save failed" },
  search_notes:              { icon: "🔍", running: "Searching notes…",         done: "Notes searched",         error: "Search failed" },
  remember_skill:            { icon: "🎓", running: "Saving skill…",            done: "Skill saved",            error: "Skill save failed" },
  recall_skill:              { icon: "🎓", running: "Loading skill…",           done: "Skill loaded",           error: "Skill load failed" },
  promote_skill:             { icon: "⬆️", running: "Upgrading skill…",         done: "Skill upgraded",         error: "Skill upgrade failed" },
  // Web
  fetch_url:                 { icon: "🌐", running: "Fetching page…",           done: "Page fetched",           error: "Fetch failed" },
  // Files
  read_file:                 { icon: "📄", running: "Reading file…",            done: "File read",              error: "Read failed" },
  write_file:                { icon: "✏️", running: "Writing file…",            done: "File saved",             error: "Write failed" },
  list_dir:                  { icon: "📁", running: "Listing directory…",       done: "Directory listed",       error: "Directory error" },
  make_download_url:         { icon: "⬇️", running: "Creating download link…",  done: "Download link ready",    error: "Link creation failed" },
  make_download_bundle:      { icon: "🗂️", running: "Bundling output…",         done: "Bundle ready",           error: "Bundle failed" },
  // Shell
  run_shell:                 { icon: "⚡", running: "Running command…",         done: "Command complete",       error: "Command failed" },
  // System
  get_time:                  { icon: "🕐", running: "Getting time…",            done: "Time retrieved",         error: "Failed" },
  platform_info:             { icon: "💻", running: "Getting system info…",     done: "System info retrieved",  error: "Failed" },
  // Browser
  browser_navigate:          { icon: "🔗", running: "Navigating…",             done: "Page loaded",            error: "Navigation failed" },
  browser_get_content:       { icon: "📋", running: "Extracting content…",      done: "Content extracted",      error: "Extraction failed" },
  browser_click:             { icon: "👆", running: "Clicking…",               done: "Clicked",                error: "Click failed" },
  browser_fill:              { icon: "⌨️", running: "Filling form…",            done: "Form filled",            error: "Fill failed" },
  browser_submit:            { icon: "📤", running: "Submitting…",             done: "Submitted",              error: "Submit failed" },
  browser_screenshot:        { icon: "📸", running: "Taking screenshot…",       done: "Screenshot taken",       error: "Screenshot failed" },
  browser_evaluate:          { icon: "⚙️", running: "Running script…",          done: "Script complete",        error: "Script failed" },
  browser_close:             { icon: "❌", running: "Closing browser…",         done: "Closed",                 error: "Close failed" },
  browser_open_for_user:     { icon: "🪟", running: "Opening browser…",         done: "Browser opened",         error: "Open failed" },
  browser_wait_for_user:     { icon: "⏳", running: "Waiting for action…",      done: "Action complete",        error: "Timed out" },
  browser_snapshot:          { icon: "🖼️", running: "Capturing page…",          done: "Page captured",          error: "Capture failed" },
  browser_click_ref:         { icon: "👆", running: "Clicking…",               done: "Clicked",                error: "Click failed" },
  browser_fill_ref:          { icon: "⌨️", running: "Filling form…",            done: "Form filled",            error: "Fill failed" },
  browser_new_tab:           { icon: "📑", running: "Opening new tab…",         done: "Tab opened",             error: "Open failed" },
  browser_list_tabs:         { icon: "📑", running: "Listing tabs…",            done: "Tabs listed",            error: "Failed" },
  browser_focus_tab:         { icon: "📑", running: "Switching tab…",           done: "Tab switched",           error: "Switch failed" },
  browser_close_tab:         { icon: "📑", running: "Closing tab…",             done: "Tab closed",             error: "Close failed" },
  browser_connect_user_chrome: { icon: "🔌", running: "Connecting browser…",   done: "Browser connected",      error: "Connection failed" },
  // Agents
  delegate_to_agent:         { icon: "🤝", running: "Delegating to agent…",    done: "Agent responded",        error: "Delegation failed" },
  list_agents:               { icon: "🤖", running: "Loading agents…",         done: "Agents loaded",          error: "Failed" },
  broadcast_to_agents:       { icon: "📡", running: "Broadcasting…",           done: "Broadcast sent",         error: "Broadcast failed" },
  run_pipeline:              { icon: "🔄", running: "Running pipeline…",        done: "Pipeline complete",      error: "Pipeline failed" },
  create_agent:              { icon: "➕", running: "Creating agent…",         done: "Agent created",          error: "Create failed" },
  delete_agent:              { icon: "🗑️", running: "Deleting agent…",         done: "Agent deleted",          error: "Delete failed" },
  update_agent:              { icon: "✏️", running: "Updating agent…",          done: "Agent updated",          error: "Update failed" },
  spawn_agent:               { icon: "🌱", running: "Spawning sub-agent…",      done: "Sub-agent started",      error: "Spawn failed" },
  run_hierarchical:          { icon: "🏗️", running: "Running hierarchy…",       done: "Hierarchy complete",     error: "Task failed" },
  // Todos
  add_todo:                  { icon: "✅", running: "Adding todo…",             done: "Todo added",             error: "Add failed" },
  list_todos:                { icon: "📋", running: "Loading todos…",           done: "Todos loaded",           error: "Failed" },
  complete_todo:             { icon: "✅", running: "Completing todo…",         done: "Marked complete",        error: "Failed" },
  // Skills
  list_skills:               { icon: "🎒", running: "Loading skills…",         done: "Skills loaded",          error: "Failed" },
  use_skill:                 { icon: "🎓", running: "Running skill…",           done: "Skill complete",         error: "Skill failed" },
  // Email
  list_emails:               { icon: "📬", running: "Fetching emails…",         done: "Emails fetched",         error: "Fetch failed" },
  read_email:                { icon: "📧", running: "Reading email…",           done: "Email read",             error: "Read failed" },
  send_email:                { icon: "📤", running: "Sending email…",           done: "Email sent",             error: "Send failed" },
  search_emails:             { icon: "🔍", running: "Searching emails…",        done: "Emails found",           error: "Search failed" },
  mark_email_read:           { icon: "✉️", running: "Marking email…",           done: "Email marked",           error: "Mark failed" },
  move_email:                { icon: "📂", running: "Moving email…",            done: "Email moved",            error: "Move failed" },
  reply_email:               { icon: "↩️", running: "Sending reply…",           done: "Reply sent",             error: "Reply failed" },
  delete_email:              { icon: "🗑️", running: "Deleting email…",          done: "Email deleted",          error: "Delete failed" },
  forward_email:             { icon: "↪️", running: "Forwarding email…",        done: "Email forwarded",        error: "Forward failed" },
  list_email_folders:        { icon: "📁", running: "Listing folders…",         done: "Folders listed",         error: "Failed" },
};

export function _toolLabel(name) {
  return TOOL_LABELS[name] || { icon: "⚙️", running: "Processing…", done: "Done", error: "Failed" };
}

// ── Active round state ─────────────────────────────────────────────────────
let _activeRoundEl = null;

/** Reset the active round pointer (called from chat.js on session reset). */
export function resetActiveRound() { _activeRoundEl = null; }

// ── Tool-line bubbles ───────────────────────────────────────────────────────

export function insertToolBubble(data) {
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;

  const el = document.createElement("div");
  el.className = "tool-line";

  if (isDevMode()) {
    el.innerHTML = `<span class="tl-name">⚙ ${escHtml(data.tool || "tool")}</span>`
                 + `<span class="tl-status">running…</span>`;
  } else {
    const lbl = _toolLabel(data.tool || "");
    el.innerHTML = `<span class="tl-label">${lbl.icon} ${escHtml(lbl.running)}</span>`;
  }

  const _tlParent = _activeRoundEl || els.messages;
  _tlParent.appendChild(el);

  if (data.call_id) {
    state._toolBubbles[data.call_id] = el;
  } else if (data.tool) {
    if (!state._toolPendingByName[data.tool]) state._toolPendingByName[data.tool] = [];
    state._toolPendingByName[data.tool].push(el);
  }
  state._toolIndex++;
  _pinThinkingMsgToBottom();
  _scrollToBottom();
}

export function updateToolBubble(data) {
  let el = null;
  if (data.call_id && state._toolBubbles[data.call_id]) {
    el = state._toolBubbles[data.call_id];
  } else if (data.tool && state._toolPendingByName[data.tool]?.length) {
    el = state._toolPendingByName[data.tool].shift();
  }
  if (!el) return;

  const raw = typeof data.result === "string" ? data.result : prettyJson(data.result);
  renderToolResult(el, data.tool || "tool", raw, !!data.is_error);
}

export function renderToolResult(el, toolName, raw, isError = false) {
  const preview    = raw.replace(/\s+/g, " ").trim().slice(0, 100);
  const expandable = raw.length > 100 || raw.includes("\n");
  const rendered   = renderMarkdown(raw);
  const hasDownload = /class="dl-link/.test(rendered);
  el.className     = isError ? "tool-line has-error" : "tool-line has-result";

  if (isDevMode()) {
    const statusIcon = isError
      ? `<span class="tl-err">✗</span>`
      : `<span class="tl-done">✓</span>`;
    el.innerHTML = `<span class="tl-name">⚙ ${escHtml(toolName)}</span>`
                 + `<span class="tl-result">${escHtml(preview)}</span>`
                 + statusIcon
                 + ((expandable || hasDownload) ? `<span class="tl-expand">›</span><div class="tl-body">${rendered}</div>` : "");
    if (expandable || hasDownload) {
      el.addEventListener("click", () => el.classList.toggle("expanded"));
    }
    if (hasDownload && !isError) el.classList.add("expanded");
  } else {
    const lbl  = _toolLabel(toolName);
    const text = isError ? lbl.error : lbl.done;
    const errMark = isError ? ` <span class="tl-err">✗</span>` : "";
    const detailHtml = (expandable || hasDownload)
      ? `<span class="tl-detail-btn" role="button" tabindex="0">${hasDownload && !expandable ? "· Download" : "· Details"}</span><div class="tl-body">${rendered}</div>`
      : "";
    el.innerHTML = `<span class="tl-label">${lbl.icon} ${escHtml(text)}</span>`
                 + errMark
                 + detailHtml;
    if (expandable || hasDownload) {
      const btn = el.querySelector(".tl-detail-btn");
      if (btn) {
        const toggle = () => {
          el.classList.toggle("expanded");
          btn.textContent = el.classList.contains("expanded")
            ? "· Collapse"
            : (hasDownload && !expandable ? "· Download" : "· Details");
        };
        btn.addEventListener("click",   toggle);
        btn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") toggle(); });
      }
    }
    if (hasDownload && !isError) el.classList.add("expanded");
  }

  const _roundContainer = el.closest(".tool-round");
  if (_roundContainer) _refreshRoundSummary(_roundContainer);

  if (isError && !isDevMode()) {
    const cpBtn = document.createElement("button");
    cpBtn.type = "button";
    cpBtn.className = "tl-copy-err-btn";
    cpBtn.title = "Copy error";
    cpBtn.innerHTML = `<svg width="9" height="9" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`;
    cpBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(raw).then(() => {
        const origHtml = cpBtn.innerHTML;
        cpBtn.textContent = "✓";
        setTimeout(() => { cpBtn.innerHTML = origHtml; }, 1400);
      }).catch(() => {});
    });
    el.appendChild(cpBtn);
  }
}

// ── Tool round blocks ───────────────────────────────────────────────────────

function _copyRoundContent(container, btn) {
  const lines = container.querySelectorAll(".tool-line");
  const parts = [];
  for (const line of lines) {
    const label = line.querySelector(".tl-label")?.textContent?.trim() || "";
    const body  = line.querySelector(".tl-body")?.textContent?.trim() || "";
    if (label) parts.push(body ? `${label}\n${body}` : label);
  }
  navigator.clipboard.writeText(parts.join("\n---\n")).then(() => {
    const orig = btn.innerHTML;
    btn.textContent = "✓";
    setTimeout(() => { btn.innerHTML = orig; }, 1400);
  }).catch(() => {});
}

function _refreshRoundSummary(roundEl) {
  const summary   = roundEl.querySelector(".tr-summary");
  const errorHint = roundEl.querySelector(".tr-error-hint");
  if (!summary) return;

  const toolLines = roundEl.querySelectorAll(".tool-line");
  const iconCount = {};
  let firstErrorText = "";
  let allSettled = true;

  for (const line of toolLines) {
    const labelEl = line.querySelector(".tl-label");
    if (!labelEl) continue;
    const text = labelEl.textContent.trim();
    const m = text.match(/^(\p{Emoji_Presentation}|\p{Extended_Pictographic}|\S+)/u);
    const icon = m ? m[1] : "⚙";
    iconCount[icon] = (iconCount[icon] || 0) + 1;
    if (line.classList.contains("has-error") && !firstErrorText) {
      const bodyEl = line.querySelector(".tl-body");
      firstErrorText = bodyEl?.textContent?.trim().split("\n")[0]?.slice(0, 80) || "Failed";
    }
    if (!line.classList.contains("has-result") && !line.classList.contains("has-error")) {
      allSettled = false;
    }
  }

  if (toolLines.length === 0) {
    summary.textContent = "⠋ Processing…";
  } else {
    const parts = Object.entries(iconCount).map(([ic, n]) => n > 1 ? `${ic} ×${n}` : ic);
    summary.textContent = (allSettled ? "" : "⠋ ") + parts.join("  ·  ");
  }

  if (errorHint) {
    errorHint.textContent = firstErrorText;
    errorHint.style.display = firstErrorText ? "" : "none";
  }
}

function _finalizeRound(roundEl) {
  if (!roundEl) return;
  roundEl.classList.add("collapsed");
  _refreshRoundSummary(roundEl);
}

export function finalizeActiveRound() {
  if (!_activeRoundEl) return;
  _finalizeRound(_activeRoundEl.closest(".tool-round"));
  _activeRoundEl = null;
}

export function createToolRound(round, maxRounds) {
  if (isDevMode()) {
    finalizeActiveRound();
    insertRoundLine(round, maxRounds);
    return;
  }

  finalizeActiveRound();

  const container = document.createElement("div");
  container.className = "tool-round";
  container.dataset.round = round;

  const header = document.createElement("div");
  header.className = "tool-round-header";

  const toggle = document.createElement("span");
  toggle.className = "tr-toggle";

  const summary = document.createElement("span");
  summary.className = "tr-summary";
  summary.textContent = "⠋ Processing…";

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "tr-copy-btn";
  copyBtn.title = "Copy tool output";
  copyBtn.innerHTML = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`;
  copyBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    _copyRoundContent(container, copyBtn);
  });

  header.appendChild(toggle);
  header.appendChild(summary);
  header.appendChild(copyBtn);

  const errorHint = document.createElement("div");
  errorHint.className = "tr-error-hint";
  errorHint.style.display = "none";

  const body = document.createElement("div");
  body.className = "tool-round-body";

  container.appendChild(header);
  container.appendChild(errorHint);
  container.appendChild(body);

  header.addEventListener("click", (e) => {
    if (e.target === copyBtn || copyBtn.contains(e.target)) return;
    container.classList.toggle("collapsed");
  });

  els.messages.appendChild(container);
  _activeRoundEl = body;
  _scrollToBottom();
}

export function insertRoundLine(round, maxRounds) {
  const el = document.createElement("div");
  el.className = "round-line";
  if (isDevMode()) {
    const maxStr = maxRounds > 0 ? `/${maxRounds}` : "";
    el.textContent = `↺  round ${round}${maxStr}`;
  } else {
    el.textContent = "🔄  Continuing…";
  }
  els.messages.appendChild(el);
  _scrollToBottom();
}
