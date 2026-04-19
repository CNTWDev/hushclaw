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
  recall:                    { icon: "💭", running: "查阅记忆库…",        done: "已检索相关内容",   error: "记忆检索失败" },
  remember:                  { icon: "📝", running: "记录到记忆库…",      done: "已保存到记忆",     error: "记录失败" },
  search_notes:              { icon: "🔍", running: "搜索笔记…",          done: "笔记搜索完成",     error: "搜索失败" },
  remember_skill:            { icon: "🎓", running: "学习新技能…",        done: "技能已记录",       error: "技能记录失败" },
  recall_skill:              { icon: "🎓", running: "调取技能知识…",      done: "已获取技能",       error: "技能调取失败" },
  promote_skill:             { icon: "⬆️", running: "升级技能包…",        done: "技能已升级",       error: "技能升级失败" },
  // Web
  fetch_url:                 { icon: "🌐", running: "获取网页内容…",      done: "已获取网页",       error: "网页获取失败" },
  // Files
  read_file:                 { icon: "📄", running: "读取文件…",          done: "已读取文件",       error: "文件读取失败" },
  write_file:                { icon: "✏️", running: "写入文件…",          done: "文件已保存",       error: "文件写入失败" },
  list_dir:                  { icon: "📁", running: "浏览目录…",          done: "目录已列出",       error: "目录访问失败" },
  make_download_url:         { icon: "⬇️", running: "生成下载链接…",      done: "下载链接已生成",   error: "链接生成失败" },
  make_download_bundle:      { icon: "🗂️", running: "注册目录产物…",      done: "目录入口已生成",   error: "目录注册失败" },
  // Shell
  run_shell:                 { icon: "⚡", running: "执行命令…",          done: "命令执行完成",     error: "命令执行失败" },
  // System
  get_time:                  { icon: "🕐", running: "获取当前时间…",      done: "已获取时间",       error: "获取失败" },
  platform_info:             { icon: "💻", running: "获取系统信息…",      done: "已获取系统信息",   error: "获取失败" },
  // Browser
  browser_navigate:          { icon: "🔗", running: "正在打开页面…",      done: "页面已加载",       error: "页面加载失败" },
  browser_get_content:       { icon: "📋", running: "提取页面内容…",      done: "内容已提取",       error: "内容提取失败" },
  browser_click:             { icon: "👆", running: "点击元素…",          done: "点击成功",         error: "点击失败" },
  browser_fill:              { icon: "⌨️", running: "填写表单…",          done: "填写完成",         error: "填写失败" },
  browser_submit:            { icon: "📤", running: "提交表单…",          done: "提交成功",         error: "提交失败" },
  browser_screenshot:        { icon: "📸", running: "截图中…",            done: "截图完成",         error: "截图失败" },
  browser_evaluate:          { icon: "⚙️", running: "执行页面脚本…",      done: "脚本执行完成",     error: "脚本执行失败" },
  browser_close:             { icon: "❌", running: "关闭浏览器…",        done: "已关闭",           error: "关闭失败" },
  browser_open_for_user:     { icon: "🪟", running: "打开浏览器窗口…",    done: "窗口已打开",       error: "打开失败" },
  browser_wait_for_user:     { icon: "⏳", running: "等待您的操作…",      done: "操作完成",         error: "操作超时" },
  browser_snapshot:          { icon: "🖼️", running: "获取页面结构…",      done: "结构已获取",       error: "获取失败" },
  browser_click_ref:         { icon: "👆", running: "点击元素…",          done: "点击成功",         error: "点击失败" },
  browser_fill_ref:          { icon: "⌨️", running: "填写表单…",          done: "填写完成",         error: "填写失败" },
  browser_new_tab:           { icon: "📑", running: "打开新标签页…",      done: "新标签已打开",     error: "打开失败" },
  browser_list_tabs:         { icon: "📑", running: "列出标签页…",        done: "已获取标签列表",   error: "获取失败" },
  browser_focus_tab:         { icon: "📑", running: "切换标签页…",        done: "标签已切换",       error: "切换失败" },
  browser_close_tab:         { icon: "📑", running: "关闭标签页…",        done: "标签已关闭",       error: "关闭失败" },
  browser_connect_user_chrome: { icon: "🔌", running: "连接浏览器…",      done: "浏览器已连接",     error: "连接失败" },
  // Agents
  delegate_to_agent:         { icon: "🤝", running: "转交给智能体…",      done: "智能体响应完成",   error: "转交失败" },
  list_agents:               { icon: "🤖", running: "获取智能体列表…",    done: "已获取智能体列表", error: "获取失败" },
  broadcast_to_agents:       { icon: "📡", running: "广播消息…",          done: "广播完成",         error: "广播失败" },
  run_pipeline:              { icon: "🔄", running: "运行流水线…",         done: "流水线完成",       error: "流水线失败" },
  create_agent:              { icon: "➕", running: "创建智能体…",        done: "智能体已创建",     error: "创建失败" },
  delete_agent:              { icon: "🗑️", running: "删除智能体…",        done: "智能体已删除",     error: "删除失败" },
  update_agent:              { icon: "✏️", running: "更新智能体配置…",    done: "配置已更新",       error: "更新失败" },
  spawn_agent:               { icon: "🌱", running: "启动子智能体…",      done: "子智能体已启动",   error: "启动失败" },
  run_hierarchical:          { icon: "🏗️", running: "运行层级任务…",      done: "层级任务完成",     error: "任务失败" },
  // Todos
  add_todo:                  { icon: "✅", running: "添加待办…",          done: "待办已添加",       error: "添加失败" },
  list_todos:                { icon: "📋", running: "获取待办列表…",      done: "已获取待办",       error: "获取失败" },
  complete_todo:             { icon: "✅", running: "完成待办…",          done: "已标记完成",       error: "操作失败" },
  // Skills
  list_skills:               { icon: "🎒", running: "获取技能列表…",      done: "已获取技能列表",   error: "获取失败" },
  use_skill:                 { icon: "🎓", running: "调用技能…",          done: "技能调用完成",     error: "技能调用失败" },
};

export function _toolLabel(name) {
  return TOOL_LABELS[name] || { icon: "⚙️", running: "处理中…", done: "完成", error: "失败" };
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
      ? `<span class="tl-detail-btn" role="button" tabindex="0">${hasDownload && !expandable ? "· 下载" : "· 详情"}</span><div class="tl-body">${rendered}</div>`
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
            ? "· 收起"
            : (hasDownload && !expandable ? "· 下载" : "· 详情");
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
    cpBtn.title = "复制错误信息";
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
      firstErrorText = bodyEl?.textContent?.trim().split("\n")[0]?.slice(0, 80) || "失败";
    }
    if (!line.classList.contains("has-result") && !line.classList.contains("has-error")) {
      allSettled = false;
    }
  }

  if (toolLines.length === 0) {
    summary.textContent = "⠋ 处理中…";
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
  summary.textContent = "⠋ 处理中…";

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "tr-copy-btn";
  copyBtn.title = "复制过程内容";
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
    el.textContent = "🔄  继续思考中…";
  }
  els.messages.appendChild(el);
  _scrollToBottom();
}
