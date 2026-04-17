/**
 * chat.js — Chat message rendering, markdown, thinking indicator, session history.
 */

import {
  state, els, SPINNERS, escHtml, prettyJson, showToast,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { renderMarkdown, renderMarkdownWithSourceMap } from "./markdown.js";
import { openDialog, closeModal } from "./modal.js";

let _spinIdx = 0;
const HTML2CANVAS_URL = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
let _html2canvasLoading = null;
let _selectionSharePopover = null;
let _selectionShareState = null;
let _selectionShareBound = false;

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

function _toolLabel(name) {
  return TOOL_LABELS[name] || { icon: "⚙️", running: "处理中…", done: "完成", error: "失败" };
}

// Show / hide all share-forum buttons when auth state changes.
document.addEventListener("hc:forum-ready", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = ""; });
});
document.addEventListener("hc:forum-unauthed", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = "none"; });
});

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ── Message bubble factory ─────────────────────────────────────────────────

export function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  msgEl.dataset.role = kind;

  const innerEl = document.createElement("div");
  innerEl.className = "msg-inner";

  const avatarEl = document.createElement("span");
  avatarEl.className = "msg-avatar";
  if (kind === "user") avatarEl.textContent = "You";
  else if (kind === "ai") avatarEl.textContent = "AI";
  else if (kind === "system") avatarEl.textContent = "SYS";
  else avatarEl.textContent = "!";

  const contentEl = document.createElement("div");
  contentEl.className = "msg-content";

  const metaEl = document.createElement("div");
  metaEl.className = "msg-meta";
  if (kind === "user") metaEl.textContent = "You";
  else if (kind === "ai") metaEl.textContent = "Assistant";
  else if (kind === "system") metaEl.textContent = "System";
  else metaEl.textContent = "Error";

  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  contentEl.appendChild(metaEl);
  contentEl.appendChild(bubbleEl);
  innerEl.appendChild(avatarEl);
  innerEl.appendChild(contentEl);
  msgEl.appendChild(innerEl);
  return { msgEl, bubbleEl, metaEl, contentEl };
}

function setCopyBtnTempText(btn, html, fallbackHtml) {
  const prev = btn.innerHTML;
  btn.innerHTML = html;
  setTimeout(() => { btn.innerHTML = fallbackHtml || prev || ""; }, 1400);
}

function getCopyImageErrorMessage(err) {
  const msg = String(err?.message || err || "");
  const lower = msg.toLowerCase();
  if (lower.includes("notallowederror") || lower.includes("permission")) {
    return "Copy image failed: clipboard permission denied by browser.";
  }
  if (lower.includes("clipboarditem") || lower.includes("clipboard")) {
    return "Copy image failed: browser does not support image clipboard write.";
  }
  if (lower.includes("failed to load html2canvas")) {
    return "Copy image failed: fallback renderer could not be loaded (network/CSP).";
  }
  if (lower.includes("canvas") || lower.includes("png")) {
    return "Copy image failed: canvas render/export error.";
  }
  if (lower.includes("foreignobject") || lower.includes("svg")) {
    return "Copy image failed: browser could not rasterize styled content.";
  }
  return `Copy image failed: ${msg || "unknown error"}`;
}

async function renderNodeToPngBlob(node) {
  const rect = node.getBoundingClientRect();
  // getBoundingClientRect returns 0 for off-screen elements; fall back to scrollWidth/Height.
  const width  = Math.max(1, Math.ceil(rect.width  || node.scrollWidth  || 720));
  const height = Math.max(1, Math.ceil(rect.height || node.scrollHeight || 200));
  const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));

  const cloned = node.cloneNode(true);
  cloned.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  const xhtml = new XMLSerializer().serializeToString(cloned);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <foreignObject width="100%" height="100%">${xhtml}</foreignObject>
    </svg>
  `;
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = reject;
      i.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * scale);
    canvas.height = Math.ceil(height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas context unavailable");
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise((resolve, reject) => {
      canvas.toBlob((png) => {
        if (png) resolve(png);
        else reject(new Error("PNG encoding failed"));
      }, "image/png");
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function ensureHtml2Canvas() {
  if (window.html2canvas) return window.html2canvas;
  if (_html2canvasLoading) return _html2canvasLoading;
  _html2canvasLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = HTML2CANVAS_URL;
    s.async = true;
    s.onload = () => {
      if (window.html2canvas) resolve(window.html2canvas);
      else reject(new Error("html2canvas loaded but unavailable"));
    };
    s.onerror = () => reject(new Error("Failed to load html2canvas"));
    document.head.appendChild(s);
  });
  return _html2canvasLoading;
}


async function renderNodeToPngBlobWithHtml2Canvas(node) {
  const html2canvas = await ensureHtml2Canvas();
  // backgroundColor must be explicit — null causes transparent background which
  // bleeds as white in some browsers. Match the card's current mode.
  const isLight = node.dataset?.mode === "light";
  const bgColor = node.classList.contains("cimg-card")
    ? (isLight ? "#f8f9fc" : "#14161f")
    : null;
  const canvas = await html2canvas(node, {
    backgroundColor: bgColor,
    scale: Math.min(3, Math.max(2, window.devicePixelRatio || 2)),
    useCORS: true,
    logging: false,
    allowTaint: false,
  });
  return await new Promise((resolve, reject) => {
    canvas.toBlob((png) => {
      if (png) resolve(png);
      else reject(new Error("PNG encoding failed"));
    }, "image/png");
  });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function _mk(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function _fmtShareDatetime(msgEl) {
  const timeEl  = msgEl?.querySelector(".msg-time");
  const timeTxt = timeEl?.textContent?.trim() || "";
  const now     = new Date();
  const yyyy    = now.getFullYear();
  const mm      = String(now.getMonth() + 1).padStart(2, "0");
  const dd      = String(now.getDate()).padStart(2, "0");
  const today   = `${yyyy}-${mm}-${dd}`;
  if (timeTxt.includes("-")) {
    // "04-08 14:32" → "2026-04-08 14:32"
    return `${yyyy}-${timeTxt}`;
  }
  if (timeTxt) return `${today} ${timeTxt}`;
  const hhmm = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${today} ${hhmm}`;
}

// ── Share helpers ──────────────────────────────────────────────────────────

function _getPrevUserText(msgEl) {
  let prev = msgEl.previousElementSibling;
  while (prev) {
    if (prev.classList.contains("user")) {
      const ub = prev.querySelector(".bubble");
      return (ub?._raw ?? ub?.innerText ?? "").trim();
    }
    prev = prev.previousElementSibling;
  }
  return "";
}

function _getPrevUserMsgEl(msgEl) {
  let prev = msgEl.previousElementSibling;
  while (prev) {
    if (prev.classList.contains("user")) return prev;
    prev = prev.previousElementSibling;
  }
  return null;
}

function _closestBubble(node) {
  if (!node) return null;
  const el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return el?.closest?.(".bubble") || null;
}

function _escapeSelectionHtml(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!normalized) return "";
  return normalized
    .split(/\n{2,}/)
    .map((block) => `<p>${escHtml(block).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function _selectionHtmlFromRange(range) {
  try {
    const wrapper = document.createElement("div");
    wrapper.appendChild(range.cloneContents());
    wrapper.querySelectorAll(
      ".msg-actions-footer, .msg-copy-actions, .msg-copy-btn, .thinking-toggle, .selection-share-popover, button"
    ).forEach((el) => el.remove());

    const html = wrapper.innerHTML.trim();
    if (!html) return "";
    if (!/<(p|ul|ol|pre|blockquote|table|h[1-6]|div|hr|li)\b/i.test(html)) {
      return `<p>${html}</p>`;
    }
    return html;
  } catch {
    return "";
  }
}

function _getSelectionBlocks(range, bubbleEl) {
  const selectors = "pre, blockquote, table, li, p, h1, h2, h3, h4, h5, h6, hr";
  const nodes = Array.from(bubbleEl.querySelectorAll(selectors));
  const picked = [];
  for (const node of nodes) {
    try {
      if (!range.intersectsNode(node)) continue;
    } catch {
      continue;
    }
    if (picked.some((prev) => prev.contains(node) || node.contains(prev))) continue;
    picked.push(node);
  }
  return picked;
}

function _selectionHtmlFromBlocks(blocks) {
  const wrapper = document.createElement("div");
  for (const block of blocks) {
    const clone = block.cloneNode(true);
    clone.querySelectorAll?.(
      ".msg-actions-footer, .msg-copy-actions, .msg-copy-btn, .thinking-toggle, .selection-share-popover, button"
    ).forEach((el) => el.remove());
    wrapper.appendChild(clone);
  }
  return wrapper.innerHTML.trim();
}

function _selectionTextFromBlocks(blocks) {
  return blocks
    .map((block) => (block.innerText || block.textContent || "").trim())
    .filter(Boolean)
    .join("\n\n");
}

// ---------------------------------------------------------------------------
// HTML → Markdown converter (for clipboard copy of selected rendered content)
// Handles the subset produced by our own markdown renderer:
// headings, bold/italic, inline code, fenced code blocks, blockquotes,
// unordered/ordered lists, links, paragraphs, horizontal rules.
// ---------------------------------------------------------------------------

function _htmlToMd(html) {
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return _nodeToMd(tmp).replace(/\n{3,}/g, "\n\n").trim();
}

function _nodeToMd(node, ctx = {}) {
  if (node.nodeType === Node.TEXT_NODE) {
    const t = node.textContent || "";
    return ctx.pre ? t : t.replace(/\n+/g, " ");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";

  const tag = node.tagName.toLowerCase();
  const inner = () => Array.from(node.childNodes).map((n) => _nodeToMd(n, ctx)).join("");

  // Block elements
  const hMatch = tag.match(/^h([1-6])$/);
  if (hMatch) return `\n\n${"#".repeat(+hMatch[1])} ${inner().trim()}\n\n`;

  if (tag === "p") {
    const t = inner().trim();
    return t ? `\n\n${t}\n\n` : "";
  }
  if (tag === "hr") return "\n\n---\n\n";
  if (tag === "br") return ctx.pre ? "\n" : "  \n";

  if (tag === "pre") {
    const codeEl = node.querySelector("code");
    const lang = (codeEl?.className || "").replace(/^language-/, "").trim();
    const code = (codeEl ? codeEl.textContent : node.textContent) || "";
    return `\n\n\`\`\`${lang}\n${code.replace(/\n$/, "")}\n\`\`\`\n\n`;
  }

  if (tag === "blockquote") {
    const lines = inner().trim().split("\n");
    return `\n\n${lines.map((l) => `> ${l}`).join("\n")}\n\n`;
  }

  if (tag === "ul") {
    const items = Array.from(node.children)
      .filter((c) => c.tagName.toLowerCase() === "li")
      .map((li) => `- ${_nodeToMd(li, ctx).trim()}`)
      .join("\n");
    return `\n\n${items}\n\n`;
  }

  if (tag === "ol") {
    const items = Array.from(node.children)
      .filter((c) => c.tagName.toLowerCase() === "li")
      .map((li, i) => `${i + 1}. ${_nodeToMd(li, ctx).trim()}`)
      .join("\n");
    return `\n\n${items}\n\n`;
  }

  if (tag === "li") {
    return inner().replace(/\n{2,}/g, "\n");
  }

  if (tag === "table") {
    // Emit a simple text table — no full markdown table reconstruction
    const rows = Array.from(node.querySelectorAll("tr"));
    const lines = rows.map((r) =>
      "| " + Array.from(r.querySelectorAll("th,td")).map((c) => c.textContent.trim()).join(" | ") + " |"
    );
    if (lines.length > 1) lines.splice(1, 0, lines[0].replace(/[^|]/g, "-"));
    return `\n\n${lines.join("\n")}\n\n`;
  }

  // Inline elements
  if (tag === "strong" || tag === "b") {
    const t = inner().trim();
    return t ? `**${t}**` : "";
  }
  if (tag === "em" || tag === "i") {
    const t = inner().trim();
    return t ? `*${t}*` : "";
  }
  if (tag === "del" || tag === "s") {
    const t = inner().trim();
    return t ? `~~${t}~~` : "";
  }
  if (tag === "code") {
    // inline code — skip if inside <pre> (handled above)
    const t = node.textContent || "";
    return t ? `\`${t}\`` : "";
  }
  if (tag === "a") {
    const href = node.getAttribute("href") || "";
    const t = inner().trim();
    return href && t ? `[${t}](${href})` : t;
  }

  // Containers: div, span, section, article, etc. — recurse
  return inner();
}

function _getSelectionShareableState() {
  const sel = window.getSelection?.();
  if (!sel || sel.isCollapsed || sel.rangeCount < 1) return null;

  const anchorBubble = _closestBubble(sel.anchorNode);
  const focusBubble = _closestBubble(sel.focusNode);
  if (!anchorBubble || !focusBubble || anchorBubble !== focusBubble) return null;
  if (!els.messages?.contains(anchorBubble)) return null;

  const msgEl = anchorBubble.closest(".msg");
  if (!msgEl) return null;

  const range = sel.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  if (!rect || (!rect.width && !rect.height)) return null;
  const blocks = _getSelectionBlocks(range, anchorBubble);
  const text = (blocks.length ? _selectionTextFromBlocks(blocks) : sel.toString())
    .replace(/\s+\n/g, "\n")
    .trim();
  if (!text || text.length < 8) return null;
  const html = (blocks.length
    ? _selectionHtmlFromBlocks(blocks)
    : _selectionHtmlFromRange(range)) || _escapeSelectionHtml(text);

  return {
    text,
    html,
    blocks,
    range,
    rect,
    bubbleEl: anchorBubble,
    msgEl,
    isUser: msgEl.classList.contains("user"),
    role: msgEl.dataset.role || (msgEl.classList.contains("user") ? "user" : "ai"),
    time: msgEl.querySelector(".msg-time")?.textContent?.trim() || fmtTime(new Date()),
  };
}

function _ensureSelectionSharePopover() {
  if (_selectionSharePopover) return _selectionSharePopover;
  const pop = document.createElement("div");
  pop.className = "selection-share-popover hidden";
  pop.innerHTML = `
    <button type="button" class="selection-share-btn" data-action="copy">Copy</button>
    <button type="button" class="selection-share-btn" data-action="image">Image</button>
    <button type="button" class="selection-share-btn" data-action="print">Print</button>
  `;
  pop.addEventListener("mousedown", (ev) => ev.preventDefault());
  pop.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".selection-share-btn");
    if (!btn || !_selectionShareState) return;
    ev.stopPropagation();
    const action = btn.dataset.action;
    if (action === "copy") {
      try {
        let md;
        const state = _selectionShareState;
        if (state.blocks?.length) {
          const srcs = [];
          const seen = new Set();
          for (const el of state.blocks) {
            const srcEl = el.closest("[data-md-src]");
            const enc = srcEl?.dataset?.mdSrc;
            if (enc && !seen.has(enc)) { seen.add(enc); srcs.push(decodeURIComponent(enc)); }
          }
          md = srcs.length ? srcs.join("\n\n") : _htmlToMd(state.html) || state.text;
        } else {
          md = _htmlToMd(state.html) || state.text;
        }
        try {
          await navigator.clipboard.write([
            new ClipboardItem({
              "text/plain": new Blob([md], { type: "text/plain" }),
              "text/html":  new Blob([state.html], { type: "text/html" }),
            }),
          ]);
        } catch {
          await navigator.clipboard.writeText(md);
        }
        showToast("Selected text copied.", "success");
      } catch {
        showToast("Copy failed.", "error");
      }
      _hideSelectionSharePopover();
      return;
    }
    if (action === "image") {
      const selectionState = _selectionShareState;
      _hideSelectionSharePopover();
      if (selectionState) _showSelectionTemplatePicker(selectionState, btn);
      return;
    }
    if (action === "print") {
      _exportSelectionPrint(_selectionShareState, btn);
      _hideSelectionSharePopover();
    }
  });
  document.body.appendChild(pop);
  _selectionSharePopover = pop;
  return pop;
}

function _hideSelectionSharePopover() {
  if (_selectionSharePopover) _selectionSharePopover.classList.add("hidden");
  _selectionShareState = null;
}

function _positionSelectionSharePopover(state) {
  const pop = _ensureSelectionSharePopover();
  pop.classList.remove("hidden");
  const margin = 10;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const rect = state.rect;
  const popRect = pop.getBoundingClientRect();
  let left = rect.left + (rect.width / 2) - (popRect.width / 2);
  left = Math.max(margin, Math.min(vw - popRect.width - margin, left));
  let top = rect.top - popRect.height - 10;
  if (top < margin) top = Math.min(vh - popRect.height - margin, rect.bottom + 10);
  pop.style.left = `${Math.round(left + window.scrollX)}px`;
  pop.style.top = `${Math.round(top + window.scrollY)}px`;
}

function _showSelectionSharePopover(state) {
  _selectionShareState = state;
  _positionSelectionSharePopover(state);
}

function _buildSelectionShareCard(selectionState, template = "auto") {
  const themeMode = document.documentElement.dataset.mode || "dark";
  let cardMode, cardTemplate;
  if (template === "dark")         { cardMode = "dark";  cardTemplate = "dark"; }
  else if (template === "ink")     { cardMode = "dark";  cardTemplate = "ink"; }
  else if (template === "folio")   { cardMode = "light"; cardTemplate = "folio"; }
  else if (template === "blueprint"){ cardMode = "dark"; cardTemplate = "blueprint"; }
  else if (template === "halo")    { cardMode = "dark";  cardTemplate = "halo"; }
  else { cardMode = themeMode; cardTemplate = themeMode; }

  const stage = _mk("div", "cimg-stage");
  const card = _mk("div", "cimg-card");
  card.dataset.mode = cardMode;
  card.dataset.template = cardTemplate;
  card.dataset.kind = "selection";

  const deco = _mk("div", "cimg-deco-quote");
  deco.textContent = cardTemplate === "folio" ? "❞" : "❝";
  card.appendChild(deco);
  card.appendChild(_mk("div", "cimg-brand-bar"));

  const body = _mk("div", "cimg-body");
  const kicker = _mk("div", "cimg-selection-kicker", selectionState.isUser ? "Selected from You" : "Selected from Assistant");
  const content = _mk("div", "cimg-content cimg-selection-content");
  content.innerHTML = selectionState.html;
  const source = _mk("div", "cimg-selection-source", selectionState.time);
  body.appendChild(kicker);
  body.appendChild(content);
  body.appendChild(source);
  card.appendChild(body);

  const footer = _mk("div", "cimg-footer");
  const fLeft = _mk("div", "cimg-footer-left");
  const avatar = _mk("div", "cimg-footer-avatar");
  const avatarImg = document.createElement("img");
  avatarImg.src = "/icon.svg";
  avatarImg.alt = "";
  avatarImg.decoding = "async";
  avatar.appendChild(avatarImg);
  fLeft.appendChild(avatar);
  fLeft.appendChild(_mk("div", "cimg-footer-name", "HushClaw"));
  const fRight = _mk("div", "cimg-footer-right");
  const fMeta = _mk("div", "cimg-footer-meta");
  fMeta.appendChild(_mk("div", "cimg-footer-brand", "Built with Memory, Skills, and Continuous Learning"));
  fMeta.appendChild(_mk("span", "cimg-footer-datetime", selectionState.time));
  fRight.appendChild(fMeta);
  footer.appendChild(fLeft);
  footer.appendChild(fRight);
  card.appendChild(footer);
  stage.appendChild(card);
  return { stage, card };
}

function _selectionTemplatePalette(template = "auto") {
  switch (template) {
    case "ink":
      return {
        bg: ["#000000", "#0d0d0d", "#050505"],
        text: "#f8fafc",
        sub: "rgba(255,255,255,0.54)",
        accent: "#fbbf24",
        line: "rgba(251,191,36,0.22)",
      };
    case "folio":
      return {
        bg: ["#f5efe4", "#f8f5ef", "#efe6d7"],
        text: "#2f2417",
        sub: "rgba(47,36,23,0.54)",
        accent: "#7c5a3b",
        line: "rgba(124,90,59,0.18)",
      };
    case "blueprint":
      return {
        bg: ["#07111f", "#0a1b32", "#081220"],
        text: "#e8f1ff",
        sub: "rgba(232,241,255,0.54)",
        accent: "#60a5fa",
        line: "rgba(96,165,250,0.24)",
      };
    case "halo":
      return {
        bg: ["#0f172a", "#131c31", "#0c1323"],
        text: "#f8fafc",
        sub: "rgba(248,250,252,0.56)",
        accent: "#7dd3fc",
        line: "rgba(125,211,252,0.24)",
      };
    case "dark":
    default:
      return {
        bg: ["#0d1117", "#1a2133", "#0a1020"],
        text: "#eef5ff",
        sub: "rgba(238,245,255,0.56)",
        accent: "#7dd3fc",
        line: "rgba(125,211,252,0.20)",
      };
  }
}

function _wrapCanvasText(ctx, text, maxWidth) {
  const lines = [];
  const paragraphs = String(text || "").replace(/\r\n/g, "\n").split("\n");
  for (const paragraph of paragraphs) {
    const raw = paragraph.trim();
    if (!raw) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const ch of raw) {
      const next = current + ch;
      if (ctx.measureText(next).width > maxWidth && current) {
        lines.push(current);
        current = ch;
      } else {
        current = next;
      }
    }
    if (current) lines.push(current);
  }
  return lines;
}

function _selectionCanvasBlocks(selectionState) {
  const blocks = Array.isArray(selectionState.blocks) ? selectionState.blocks : [];
  if (!blocks.length) {
    return [{ type: "p", text: selectionState.text || "" }];
  }
  return blocks.map((block) => {
    const type = String(block.tagName || "p").toLowerCase();
    return {
      type,
      text: (block.innerText || block.textContent || "").trim(),
    };
  }).filter((block) => block.text || block.type === "hr");
}

function _measureSelectionCanvasLayout(ctx, blocks, width, paddingX, topY) {
  let y = topY;
  for (const block of blocks) {
    const type = block.type;
    if (type === "hr") {
      y += 30;
      continue;
    }
    if (type === "pre") {
      ctx.font = '500 28px "SF Mono", "Fira Code", monospace';
      const codeLines = String(block.text || "").split("\n");
      y += 34 + (codeLines.length * 34) + 28;
      continue;
    }
    const font = /^h[1-6]$/.test(type)
      ? '700 50px "Inter", "PingFang SC", sans-serif'
      : '600 40px "Inter", "PingFang SC", sans-serif';
    ctx.font = font;
    const maxWidth = type === "blockquote"
      ? width - (paddingX * 2) - 42
      : width - (paddingX * 2) - (type === "li" ? 26 : 0);
    const lines = _wrapCanvasText(ctx, block.text, maxWidth);
    const lineHeight = /^h[1-6]$/.test(type) ? 58 : 48;
    y += (Math.max(1, lines.length) * lineHeight);
    y += type === "blockquote" ? 36 : 26;
  }
  return y;
}

function _drawSelectionCanvasBlock(ctx, block, palette, width, paddingX, y) {
  const type = block.type;
  if (type === "hr") {
    ctx.strokeStyle = palette.line;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(paddingX, y);
    ctx.lineTo(width - paddingX, y);
    ctx.stroke();
    return y + 30;
  }

  if (type === "pre") {
    const codeLines = String(block.text || "").split("\n");
    const boxHeight = 34 + (codeLines.length * 34) + 28;
    ctx.fillStyle = "rgba(0,0,0,0.24)";
    ctx.strokeStyle = palette.line;
    ctx.lineWidth = 1.5;
    const boxY = y - 8;
    const boxX = paddingX - 8;
    const boxW = width - (paddingX * 2) + 16;
    const radius = 16;
    ctx.beginPath();
    ctx.moveTo(boxX + radius, boxY);
    ctx.arcTo(boxX + boxW, boxY, boxX + boxW, boxY + boxHeight, radius);
    ctx.arcTo(boxX + boxW, boxY + boxHeight, boxX, boxY + boxHeight, radius);
    ctx.arcTo(boxX, boxY + boxHeight, boxX, boxY, radius);
    ctx.arcTo(boxX, boxY, boxX + boxW, boxY, radius);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = palette.text;
    ctx.font = '500 28px "SF Mono", "Fira Code", monospace';
    let cy = y + 28;
    for (const line of codeLines) {
      ctx.fillText(line, paddingX + 10, cy);
      cy += 34;
    }
    return y + boxHeight + 12;
  }

  const isHeading = /^h[1-6]$/.test(type);
  const isQuote = type === "blockquote";
  const isList = type === "li";
  const font = isHeading
    ? '700 50px "Inter", "PingFang SC", sans-serif'
    : '600 40px "Inter", "PingFang SC", sans-serif';
  ctx.font = font;

  let x = paddingX;
  let maxWidth = width - (paddingX * 2);
  if (isQuote) { x += 28; maxWidth -= 42; }
  if (isList) {
    ctx.fillStyle = palette.accent;
    ctx.beginPath();
    ctx.arc(paddingX + 7, y - 18, 5, 0, Math.PI * 2);
    ctx.fill();
    x += 26;
    maxWidth -= 26;
  }

  const lines = _wrapCanvasText(ctx, block.text, maxWidth);
  const lineHeight = isHeading ? 58 : 48;
  if (isQuote) {
    const quoteHeight = Math.max(1, lines.length) * lineHeight;
    ctx.fillStyle = palette.line;
    ctx.fillRect(paddingX, y - 34, 6, quoteHeight + 20);
  }
  ctx.fillStyle = isHeading ? palette.accent : palette.text;
  let cy = y;
  for (const line of lines) {
    if (!line) {
      cy += Math.round(lineHeight * 0.72);
      continue;
    }
    ctx.fillText(line, x, cy);
    cy += lineHeight;
  }
  return cy + (isQuote ? 18 : 10);
}

async function _renderSelectionTextToPngBlob(selectionState, template = "auto") {
  const width = 1320;
  const paddingX = 108;
  const topY = 136;
  const palette = _selectionTemplatePalette(template);
  const blocks = _selectionCanvasBlocks(selectionState);

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas context unavailable");

  const footerHeight = 128;
  const contentBottom = _measureSelectionCanvasLayout(ctx, blocks, width, paddingX, topY);
  const height = Math.max(760, contentBottom + footerHeight + 84);

  canvas.width = width;
  canvas.height = height;

  const bg = ctx.createLinearGradient(0, 0, width, height);
  bg.addColorStop(0, palette.bg[0]);
  bg.addColorStop(0.55, palette.bg[1]);
  bg.addColorStop(1, palette.bg[2]);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = palette.line;
  ctx.lineWidth = 2;
  ctx.strokeRect(28, 28, width - 56, height - 56);

  ctx.fillStyle = palette.accent;
  ctx.font = '700 22px "Inter", "PingFang SC", sans-serif';
  ctx.fillText(selectionState.isUser ? "SELECTED FROM YOU" : "SELECTED FROM ASSISTANT", paddingX, 82);

  let y = topY;
  for (const block of blocks) {
    y = _drawSelectionCanvasBlock(ctx, block, palette, width, paddingX, y);
    y += 16;
  }

  const footerY = height - 78;
  ctx.fillStyle = palette.text;
  ctx.font = '700 28px "Inter", "PingFang SC", sans-serif';
  ctx.fillText("HushClaw", paddingX, footerY);

  ctx.fillStyle = palette.sub;
  ctx.font = '600 18px "Inter", "PingFang SC", sans-serif';
  ctx.fillText("Built with Memory, Skills, and Continuous Learning", paddingX, footerY + 30);

  ctx.textAlign = "right";
  ctx.fillText(selectionState.time || fmtTime(new Date()), width - paddingX, footerY + 30);
  ctx.textAlign = "left";

  return await new Promise((resolve, reject) => {
    canvas.toBlob((png) => {
      if (png) resolve(png);
      else reject(new Error("PNG encoding failed"));
    }, "image/png");
  });
}

async function copySelectionAsImage(selectionState, btn, template = "auto") {
  const { stage, card } = _buildSelectionShareCard(selectionState, template);
  document.body.appendChild(stage);
  try {
    let blob;
    try {
      blob = await renderNodeToPngBlobWithHtml2Canvas(card);
    } catch {
      try {
        blob = await renderNodeToPngBlob(card);
      } catch {
        blob = await _renderSelectionTextToPngBlob(selectionState, template);
      }
    }
    if (navigator.clipboard?.write && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      showToast("Selected excerpt copied as image.", "success");
      return;
    }
    downloadBlob(blob, "hushclaw-selection.png");
    showToast("Clipboard image not supported. Downloaded PNG instead.", "warn");
  } finally {
    stage.remove();
  }
}

function _buildTemplatePickerHtml() {
  return `<div class="img-tpl-gallery">
    <div class="img-tpl-intro">
      <div class="img-tpl-kicker">Share Image Studio</div>
      <p class="img-tpl-note">挑一种更适合内容气质的版式。所有模板都会保留原始聊天内容，只改变视觉表达。</p>
    </div>
    <div class="img-tpl-picker">
      <button class="img-tpl-opt" data-tpl="dark" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--dark"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">雅灰</div>
            <span class="img-tpl-chip">Core</span>
          </div>
          <div class="img-tpl-subtitle">Midnight Navy</div>
          <div class="img-tpl-desc">克制、专业，适合研究结论和偏理性的长回答。</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="ink" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--ink"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">金墨</div>
            <span class="img-tpl-chip">Prestige</span>
          </div>
          <div class="img-tpl-subtitle">Gold Ink</div>
          <div class="img-tpl-desc">更有质感与仪式感，适合正式输出、观点总结和展示稿。</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="folio" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--folio"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">册页</div>
            <span class="img-tpl-chip">Editorial</span>
          </div>
          <div class="img-tpl-subtitle">Monograph Folio</div>
          <div class="img-tpl-desc">像一本精致刊物的内页，适合方法论、洞察总结和高级感长文。</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="blueprint" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--blueprint"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">蓝图</div>
            <span class="img-tpl-chip">System</span>
          </div>
          <div class="img-tpl-subtitle">Blueprint Grid</div>
          <div class="img-tpl-desc">更像技术海报和系统说明页，适合框架、架构、路线图与策略内容。</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="halo" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--halo"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">月晕</div>
            <span class="img-tpl-chip">Glass</span>
          </div>
          <div class="img-tpl-subtitle">Halo Glass</div>
          <div class="img-tpl-desc">更像一张展示海报，适合金句、总结页和适合转发的视觉型内容。</div>
        </div>
      </button>
    </div>
  </div>`;
}

/** Build enriched markdown: Q→A context header + attribution footer. */
function _buildShareMarkdown(bubbleEl, msgEl) {
  const aiText   = (bubbleEl._raw ?? bubbleEl.textContent ?? "").trim();
  const datetime = _fmtShareDatetime(msgEl);
  const userText = _getPrevUserText(msgEl);
  const lines    = [];

  if (userText) {
    lines.push(`> 💬 **提问**`);
    lines.push(`>`);
    userText.split("\n").forEach(l => lines.push(`> ${l}`));
    lines.push("");
    lines.push("---");
    lines.push("");
  }
  lines.push(aiText);
  lines.push("");
  lines.push("---");
  lines.push(`*via [HushClaw](https://github.com/hushclaw/hushclaw) · ${datetime}*`);
  return lines.join("\n");
}

/**
 * Build the off-screen share card DOM.
 * template: "auto" (follow current theme) | "dark" | "ink" | "folio" | "blueprint" | "halo"
 *
 * New structure (matching reference images):
 *   .cimg-card
 *     .cimg-deco-quote          (decorative ❝ or ❞)
 *     .cimg-body > .cimg-content
 *     .cimg-footer              (avatar + name left, brand + datetime right)
 */
function _buildShareCard(bubbleEl, msgEl, template = "auto") {
  const themeMode = document.documentElement.dataset.mode || "dark";
  const datetime  = _fmtShareDatetime(msgEl);

  let cardMode, cardTemplate;
  if (template === "dark")         { cardMode = "dark";  cardTemplate = "dark"; }
  else if (template === "ink")     { cardMode = "dark";  cardTemplate = "ink"; }
  else if (template === "folio")   { cardMode = "light"; cardTemplate = "folio"; }
  else if (template === "blueprint"){ cardMode = "dark"; cardTemplate = "blueprint"; }
  else if (template === "halo")    { cardMode = "dark";  cardTemplate = "halo"; }
  else { cardMode = themeMode; cardTemplate = themeMode; }

  const stage = _mk("div", "cimg-stage");
  const card  = _mk("div", "cimg-card");
  card.dataset.mode     = cardMode;
  card.dataset.template = cardTemplate;

  // ── Decorative quote mark ───────────────────────────────
  const deco = _mk("div", "cimg-deco-quote");
  deco.textContent = cardTemplate === "folio" ? "❞" : "❝";
  card.appendChild(deco);

  // ── Keep old brand bar hidden (CSS does display:none) ───
  const brandBar = _mk("div", "cimg-brand-bar");
  card.appendChild(brandBar);

  // ── Content body ────────────────────────────────────────
  const body    = _mk("div", "cimg-body");
  const content = _mk("div", "cimg-content");
  content.innerHTML = bubbleEl.innerHTML;
  content.querySelectorAll(".msg-actions, .copy-btn, button, .thinking-toggle, .msg-actions-footer").forEach(e => e.remove());
  body.appendChild(content);
  card.appendChild(body);

  // ── Bottom footer: avatar/name | brand/datetime ─────────
  const footer = _mk("div", "cimg-footer");

  const fLeft = _mk("div", "cimg-footer-left");
  const avatar = _mk("div", "cimg-footer-avatar");
  const avatarImg = document.createElement("img");
  avatarImg.src = "/icon.svg";
  avatarImg.alt = "";
  avatarImg.decoding = "async";
  avatar.appendChild(avatarImg);
  const fName = _mk("div", "cimg-footer-name", "HushClaw");
  fLeft.appendChild(avatar);
  fLeft.appendChild(fName);

  const fRight = _mk("div", "cimg-footer-right");
  const fRightInner = _mk("div", "cimg-footer-meta");
  const fBrand = _mk("div", "cimg-footer-brand", "Built with Memory, Skills, and Continuous Learning");
  const fDatetime = _mk("span", "cimg-footer-datetime", datetime);
  fRightInner.appendChild(fBrand);
  fRightInner.appendChild(fDatetime);
  fRight.appendChild(fRightInner);

  footer.appendChild(fLeft);
  footer.appendChild(fRight);
  card.appendChild(footer);

  stage.appendChild(card);
  return { stage, card };
}

async function copyBubbleAsImage(bubbleEl, btn, template = "auto") {
  const msgEl = bubbleEl.closest(".msg");
  const { stage, card } = _buildShareCard(bubbleEl, msgEl, template);
  document.body.appendChild(stage);
  try {
    let blob;
    try {
      blob = await renderNodeToPngBlobWithHtml2Canvas(card);
    } catch {
      blob = await renderNodeToPngBlob(card);
    }
    if (navigator.clipboard?.write && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      setCopyBtnTempText(btn, "✓ Copied", btn._origHtml || btn.innerHTML);
      return;
    }
    downloadBlob(blob, "hushclaw-message.png");
    setCopyBtnTempText(btn, "Saved", btn._origHtml || btn.innerHTML);
    showToast("Clipboard image not supported. Downloaded PNG instead.", "warn");
  } finally {
    stage.remove();
  }
}

/** Show template picker modal, then generate + copy the chosen card. */
function _showImageTemplatePicker(bubbleEl, btn) {
  const origHtml = btn._origHtml || btn.innerHTML;

  async function doGenerate(tpl) {
    setCopyBtnTempText(btn, "⏳", origHtml);
    try {
      await copyBubbleAsImage(bubbleEl, btn, tpl);
    } catch (err) {
      setCopyBtnTempText(btn, "Failed", origHtml);
      showToast(getCopyImageErrorMessage(err), "error");
    }
  }

  openDialog({
    title: "选择分享样式",
    html: _buildTemplatePickerHtml(),
    closeOnBackdrop: true,
    actions: [],
  });

  requestAnimationFrame(() => {
    document.querySelectorAll(".img-tpl-opt").forEach(opt => {
      opt.addEventListener("click", () => {
        closeModal();
        doGenerate(opt.dataset.tpl);
      });
    });
  });
}

function _showSelectionTemplatePicker(selectionState, btn) {
  const origHtml = btn?._origHtml || btn?.innerHTML || "Image";

  async function doGenerate(tpl) {
    setCopyBtnTempText(btn, "⏳", origHtml);
    try {
      await copySelectionAsImage(selectionState, btn, tpl);
    } catch (err) {
      setCopyBtnTempText(btn, "Failed", origHtml);
      showToast(getCopyImageErrorMessage(err), "error");
    }
  }

  openDialog({
    title: "选择分享样式",
    html: _buildTemplatePickerHtml(),
    closeOnBackdrop: true,
    actions: [],
  });

  requestAnimationFrame(() => {
    document.querySelectorAll(".img-tpl-opt").forEach(opt => {
      opt.addEventListener("click", () => {
        closeModal();
        doGenerate(opt.dataset.tpl);
      });
    });
  });
}

function fmtTime(d) {
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hhmm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (sameDay) return hhmm;
  const mo = (d.getMonth() + 1).toString().padStart(2, "0");
  const dd = d.getDate().toString().padStart(2, "0");
  return `${mo}-${dd} ${hhmm}`;
}


function _roleLabelFromMsg(msgEl) {
  if (msgEl.classList.contains("user")) return "You";
  if (msgEl.classList.contains("ai")) return "Assistant";
  if (msgEl.classList.contains("system")) return "System";
  if (msgEl.classList.contains("error")) return "Error";
  return "Message";
}

// ── Browser-print based PDF export (supports all languages + markdown) ─────

function _buildPrintHtml(msgs, title = "HushClaw Chat Export") {
  const now = new Date();
  const generatedAt = now.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });

  const rows = msgs.map(({ role, time, html, isUser }) => `
    <div class="msg ${isUser ? "user" : "ai"}">
      <div class="msg-header">
        <div class="msg-role-badge ${isUser ? "user" : "ai"}">${escHtml(role)}</div>
        <span class="msg-time">${escHtml(time)}</span>
      </div>
      <div class="msg-body">${html}</div>
    </div>`).join("\n");

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escHtml(title)}</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Hiragino Sans GB", "PingFang SC",
               "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif;
  font-size: 13.5px; line-height: 1.7; color: #1e293b;
  background: #f8f9fc;
}
.page-wrap { max-width: 860px; margin: 0 auto; padding: 32px 40px 60px; }

/* ── Page header ── */
.page-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 0 16px;
  border-bottom: 2px solid #e2e8f0;
  margin-bottom: 32px;
}
.ph-left { display: flex; align-items: center; gap: 10px; }
.ph-logo {
  width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0;
  background: linear-gradient(135deg, #7c6ff7 0%, #38bdf8 100%);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 800; color: #fff; letter-spacing: 0.04em;
}
.ph-info { display: flex; flex-direction: column; gap: 1px; }
.ph-name { font-size: 15px; font-weight: 800; color: #1e293b; letter-spacing: -0.02em; }
.ph-sub  { font-size: 11px; color: #64748b; }
.ph-right { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.ph-title { font-size: 12px; font-weight: 600; color: #475569; }
.ph-date  { font-size: 11px; color: #94a3b8; }

/* ── Messages ── */
.msgs { display: flex; flex-direction: column; gap: 18px; }
.msg { border-radius: 10px; page-break-inside: avoid; overflow: hidden; }
.msg.user { background: #eef2ff; border: 1px solid #c7d2fe; }
.msg.ai   { background: #ffffff; border: 1px solid #e2e8f0;
            box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
.msg-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 16px 8px;
  border-bottom: 1px solid rgba(0,0,0,0.05);
}
.msg.user .msg-header { border-bottom-color: rgba(99,102,241,0.12); }
.msg-role-badge {
  font-size: 10px; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase; padding: 2px 8px; border-radius: 20px;
}
.msg-role-badge.user { background: #e0e7ff; color: #4f46e5; }
.msg-role-badge.ai   { background: #f1f5f9; color: #475569; }
.msg-time { font-size: 11px; color: #94a3b8; }
.msg-body { padding: 14px 18px 16px; font-size: 13.5px; }
.msg-body p { margin: 0 0 9px; }
.msg-body p:last-child { margin-bottom: 0; }
.msg-body h1,.msg-body h2,.msg-body h3,.msg-body h4 { font-weight: 700; margin: 16px 0 6px; color: #0f172a; }
.msg-body h1 { font-size: 18px; } .msg-body h2 { font-size: 15px; }
.msg-body h3 { font-size: 13.5px; } .msg-body h4 { font-size: 13px; }
.msg-body ul, .msg-body ol { padding-left: 22px; margin: 6px 0; }
.msg-body li { margin: 3px 0; }
.msg-body pre {
  background: #1e293b; color: #e2e8f0;
  border-radius: 8px; padding: 14px 16px;
  overflow-x: auto; margin: 10px 0;
  font-family: "SF Mono","Fira Code","Cascadia Code","Consolas",monospace;
  font-size: 12px; line-height: 1.6;
}
.msg-body code {
  font-family: "SF Mono","Fira Code","Cascadia Code","Consolas",monospace;
  font-size: 12px;
}
.msg-body p code, .msg-body li code {
  background: #f1f5f9; color: #0e7490;
  border-radius: 4px; padding: 1px 6px;
  border: 1px solid #e2e8f0;
}
.msg-body pre code { background: none; color: inherit; border: none; padding: 0; }
.msg-body table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12.5px; }
.msg-body th { background: #f8fafc; font-weight: 600; color: #334155; padding: 8px 12px; border: 1px solid #e2e8f0; }
.msg-body td { padding: 7px 12px; border: 1px solid #e2e8f0; color: #475569; }
.msg-body tr:nth-child(even) td { background: #f8fafc; }
.msg-body blockquote {
  border-left: 3px solid #818cf8; margin: 10px 0;
  padding: 6px 14px; color: #64748b; font-style: italic;
  background: #f8f9ff; border-radius: 0 6px 6px 0;
}
.msg-body hr { border: none; border-top: 1px solid #e2e8f0; margin: 14px 0; }
.msg-body a { color: #4f46e5; text-decoration: none; }
.msg-body strong { color: #0f172a; }

/* ── Page footer ── */
.page-footer {
  margin-top: 40px; padding-top: 16px;
  border-top: 1px solid #e2e8f0;
  font-size: 11px; color: #94a3b8;
  display: flex; justify-content: space-between; align-items: center;
}
.pf-brand { display: flex; align-items: center; gap: 6px; }
.pf-logo {
  width: 18px; height: 18px; border-radius: 5px;
  background: linear-gradient(135deg, #7c6ff7 0%, #38bdf8 100%);
  display: flex; align-items: center; justify-content: center;
  font-size: 7px; font-weight: 800; color: #fff;
}

@page { margin: 16mm 18mm; }
@media print {
  body { background: #fff; }
  .page-wrap { padding: 0; }
  .msg.ai { box-shadow: none; }
}
</style>
</head>
<body>
<div class="page-wrap">
  <div class="page-header">
    <div class="ph-left">
      <div class="ph-logo">HC</div>
      <div class="ph-info">
        <span class="ph-name">HushClaw</span>
        <span class="ph-sub">Built with Memory, Skills, and Continuous Learning</span>
      </div>
    </div>
    <div class="ph-right">
      <span class="ph-title">${escHtml(title)}</span>
      <span class="ph-date">${generatedAt}</span>
    </div>
  </div>
  <div class="msgs">${rows}</div>
  <div class="page-footer">
    <div class="pf-brand">
      <div class="pf-logo">HC</div>
      <span>HushClaw · Built with Memory, Skills, and Continuous Learning</span>
    </div>
    <span>${generatedAt}</span>
  </div>
</div>
</body></html>`;
}

function _printMessages(msgs, title) {
  const html = _buildPrintHtml(msgs, title);
  const win = window.open("", "_blank", "width=900,height=700");
  if (!win) {
    showToast("Pop-up blocked. Please allow pop-ups and try again.", "warn");
    return;
  }
  win.document.write(html);
  win.document.close();
  win.focus();
  win.onload = () => { win.print(); };
}

function _exportSingleMessagePrint(msgEl, bubbleEl, btn) {
  const role   = _roleLabelFromMsg(msgEl);
  const time   = msgEl.querySelector(".msg-time")?.textContent?.trim() || fmtTime(new Date());
  const html   = bubbleEl?.innerHTML ?? "";
  const isUser = msgEl.classList.contains("user");

  const msgs = [];
  // For AI messages, prepend the user question for context
  if (!isUser) {
    const userMsgEl = _getPrevUserMsgEl(msgEl);
    if (userMsgEl) {
      const uBubble = userMsgEl.querySelector(".bubble");
      msgs.push({
        role:   "You",
        time:   userMsgEl.querySelector(".msg-time")?.textContent?.trim() || time,
        html:   uBubble?.innerHTML ?? "",
        isUser: true,
      });
    }
  }
  msgs.push({ role, time, html, isUser });

  const title = isUser ? "Your Message" : "Q&A — HushClaw";
  _printMessages(msgs, title);
  setCopyBtnTempText(btn, "Opened", btn.innerHTML || "Print");
}

function _exportSelectionPrint(selectionState, btn) {
  const title = selectionState.isUser ? "Selected Excerpt — You" : "Selected Excerpt — Assistant";
  _printMessages([{
    role: selectionState.isUser ? "You" : "Assistant",
    time: selectionState.time || fmtTime(new Date()),
    html: selectionState.html,
    isUser: !!selectionState.isUser,
  }], title);
  if (btn) setCopyBtnTempText(btn, "Opened", btn._origHtml || btn.innerHTML || "Print");
}

function _bindSelectionShare() {
  if (_selectionShareBound) return;
  _selectionShareBound = true;

  const refresh = () => {
    const state = _getSelectionShareableState();
    if (!state) {
      _hideSelectionSharePopover();
      return;
    }
    _showSelectionSharePopover(state);
  };

  document.addEventListener("selectionchange", () => {
    requestAnimationFrame(refresh);
  });
  document.addEventListener("mouseup", () => {
    requestAnimationFrame(refresh);
  });
  document.addEventListener("mousedown", (ev) => {
    if (_selectionSharePopover?.contains(ev.target)) return;
    const bubble = _closestBubble(ev.target);
    if (!bubble || !els.messages?.contains(bubble)) _hideSelectionSharePopover();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") _hideSelectionSharePopover();
  });
  els.messages?.addEventListener("scroll", _hideSelectionSharePopover, { passive: true });
  window.addEventListener("resize", _hideSelectionSharePopover, { passive: true });
  window.addEventListener("scroll", _hideSelectionSharePopover, { passive: true });
}

function addCopyActions(msgEl, bubbleEl, contentEl, ts) {
  _bindSelectionShare();
  const footer = document.createElement("div");
  footer.className = "msg-actions-footer";

  const actions = document.createElement("div");
  actions.className = "msg-copy-actions";

  const timeEl = document.createElement("span");
  timeEl.className = "msg-time";
  timeEl.textContent = fmtTime(ts instanceof Date ? ts : new Date());
  actions.appendChild(timeEl);

  const mdBtn = document.createElement("button");
  mdBtn.type = "button";
  mdBtn.className = "msg-copy-btn";
  mdBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Copy`;
  mdBtn.title = "Copy as enriched Markdown (with Q&A context + attribution)";
  mdBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const mdOrigHtml = mdBtn.innerHTML;
    const text = msgEl.dataset.role === "ai"
      ? _buildShareMarkdown(bubbleEl, msgEl)
      : (bubbleEl._raw ?? bubbleEl.textContent ?? "");
    try {
      await navigator.clipboard.writeText(text);
      setCopyBtnTempText(mdBtn, "✓ Copied", mdOrigHtml);
    } catch {
      setCopyBtnTempText(mdBtn, "Failed", mdOrigHtml);
    }
  });

  const imgBtn = document.createElement("button");
  imgBtn.type = "button";
  imgBtn.className = "msg-copy-btn";
  imgBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="10" height="10" rx="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="4" r="1" fill="currentColor"/><path d="M1 8.5l3-3 2.5 2.5 1.5-2 2.5 2.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg> Image`;
  imgBtn._origHtml = imgBtn.innerHTML;
  imgBtn.title = "Copy message as image — pick a template";
  imgBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    _showImageTemplatePicker(bubbleEl, imgBtn);
  });

  const pdfBtn = document.createElement("button");
  pdfBtn.type = "button";
  pdfBtn.className = "msg-copy-btn";
  pdfBtn.title = "Open print dialog (save as PDF)";
  pdfBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><path d="M2 2h5.5L10 4.5V10H2V2Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M7 2v3h3" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg> Print`;
  pdfBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    _exportSingleMessagePrint(msgEl, bubbleEl, pdfBtn);
  });

  actions.appendChild(mdBtn);
  actions.appendChild(imgBtn);
  actions.appendChild(pdfBtn);

  // "Share to Forum" — only shown on AI messages when forum plugin is active
  if (msgEl.dataset.role === "ai") {
    const shareBtn = document.createElement("button");
    shareBtn.type = "button";
    shareBtn.className = "msg-copy-btn share-forum-btn";
    shareBtn.title = "Share this Q&A to Knowledge";
    shareBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><circle cx="9" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="9" cy="9" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="3" cy="6" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M4.4 6.7 7.6 8.3M7.6 3.7 4.4 5.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> 分享到社区`;
    shareBtn.style.display = "none"; // hidden until forum plugin reports ready
    shareBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _shareToForum(msgEl, bubbleEl, shareBtn);
    });
    actions.appendChild(shareBtn);

    // If forum is already visible when this message renders, show immediately
    if (document.querySelector('nav.tabs [data-tab="forum"]')?.style.display !== "none"
        && document.querySelector('nav.tabs [data-tab="forum"]')) {
      shareBtn.style.display = "";
    }
  }

  footer.appendChild(timeEl);
  footer.appendChild(actions);
  contentEl.appendChild(footer);
}

function _shareToForum(msgEl, bubbleEl, btn) {
  // Extract Q (previous user message) + A (this AI bubble)
  const aiText   = (bubbleEl._raw ?? bubbleEl.innerText ?? "").trim();
  let userText   = "";
  // Walk backwards to find the immediately preceding user message
  let prev = msgEl.previousElementSibling;
  while (prev) {
    if (prev.classList.contains("user")) {
      const ub = prev.querySelector(".bubble");
      userText = (ub?._raw ?? ub?.innerText ?? "").trim();
      break;
    }
    prev = prev.previousElementSibling;
  }

  const title   = userText.length > 120 ? userText.slice(0, 117) + "…" : userText;
  const content = userText
    ? `**提问：**\n\n${userText}\n\n---\n\n**回复：**\n\n${aiText}`
    : aiText;

  // Lazy-import to avoid hard dependency on optional forum plugin
  import("../transsion/forum.js")
    .then(({ openComposeWith }) => {
      import("./panels.js").then(({ switchTab }) => {
        switchTab("forum");
        // Give the tab a tick to render before filling
        requestAnimationFrame(() => openComposeWith(title, content));
      });
    })
    .catch(() => {
      import("./state.js").then(({ showToast }) =>
        showToast("社区论坛插件未加载，请先登录 Transsion 账号。", "warn")
      );
    });

  setCopyBtnTempText(btn, "已跳转 ✓", btn.innerHTML);
}

export function exportCurrentSessionAsPdf(btn = null) {
  const msgs = [];
  const msgEls = Array.from(els.messages.querySelectorAll(".msg"));
  for (const msgEl of msgEls) {
    const bubbleEl = msgEl.querySelector(".bubble");
    if (!bubbleEl || bubbleEl.classList.contains("thinking-bubble")) continue;
    const html = bubbleEl.innerHTML;
    if (!html?.trim()) continue;
    msgs.push({
      role: _roleLabelFromMsg(msgEl),
      time: msgEl.querySelector(".msg-time")?.textContent?.trim() || "",
      html,
      isUser: msgEl.classList.contains("user"),
    });
  }
  if (!msgs.length) {
    showToast("No chat messages to export yet.", "warn");
    return;
  }
  _printMessages(msgs, "HushClaw Chat Export");
  if (btn) setCopyBtnTempText(btn, "Opened", btn.innerHTML || "Export");
}

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text) {
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
  bubbleEl.classList.add("markdown-body");
  bubbleEl._raw = text;
  bubbleEl.innerHTML = renderMarkdownWithSourceMap(text);

export function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

export function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

// ── Streaming AI response ──────────────────────────────────────────────────

export function appendChunk(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdownWithSourceMap(state._aiBubbleEl._raw);
  pinThinkingMsgToBottom();
  scrollToBottom();
}

/**
 * Replace (not append) the current in-progress AI bubble with *text*.
 * Used during session replay to restore accumulated text without duplication.
 */
export function setChunkText(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = text;
  state._aiBubbleEl.innerHTML = renderMarkdownWithSourceMap(text);
  pinThinkingMsgToBottom();
  scrollToBottom();
}

export function finalizeAiMsg() {
  removeThinkingMsg();
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;
}

// ── Tool call / result bubbles ─────────────────────────────────────────────

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

  els.messages.appendChild(el);

  if (data.call_id) {
    state._toolBubbles[data.call_id] = el;
  } else if (data.tool) {
    if (!state._toolPendingByName[data.tool]) state._toolPendingByName[data.tool] = [];
    state._toolPendingByName[data.tool].push(el);
  }
  state._toolIndex++;
  pinThinkingMsgToBottom();
  scrollToBottom();
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
  scrollToBottom();
}

// ── Thinking indicator ─────────────────────────────────────────────────────

export function insertThinkingMsg(startTime = Date.now()) {
  removeThinkingMsg();
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  bubbleEl.classList.add("thinking-bubble");
  bubbleEl.textContent = "⠋ thinking…";
  els.messages.appendChild(msgEl);
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = startTime;
  state._thinkingTimer = setInterval(() => {
    if (!state._thinkingEl) return;
    const sec  = Math.floor((Date.now() - state._thinkingStart) / 1000);
    const spin = SPINNERS[_spinIdx++ % SPINNERS.length];
    bubbleEl.textContent = `${spin} thinking ${sec}s`;
  }, 100);
}

export function removeThinkingMsg() {
  if (state._thinkingTimer) { clearInterval(state._thinkingTimer); state._thinkingTimer = null; }
  if (state._thinkingEl)    { state._thinkingEl.remove(); state._thinkingEl = null; }
}

export function pinThinkingMsgToBottom() {
  if (state._thinkingEl) {
    els.messages.appendChild(state._thinkingEl);
  }
}

export function hasVisibleInProgressMarker() {
  if (state._thinkingEl && state._thinkingEl.isConnected) return true;
  return !!els.messages.querySelector(".tool-line:not(.has-result)");
}

export function rehydrateInProgressUi(sessionId) {
  if (!isSessionRunning(sessionId)) return;
  const startedAt = state._sessionRunState[sessionId]?.startedAt || Date.now();
  if (!hasVisibleInProgressMarker()) {
    insertThinkingMsg(startedAt);
    return;
  }
  // Marker already exists (tool bubbles) — just correct _thinkingStart so the
  // existing timer (if any) reflects the real session start time.
  state._thinkingStart = startedAt;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

// Markdown rendering is implemented in modules/markdown.js

// ── Session history restore ────────────────────────────────────────────────

function _fmtHistoryTs(rawTs) {
  const ts = Number(rawTs || 0);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function _renderSessionSummary(summary) {
  if (!summary) return;
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("markdown-body", "session-history-summary");
  bubbleEl._raw = summary;
  bubbleEl.innerHTML = `<div class="session-history-label">Compaction Summary</div>${renderMarkdownWithSourceMap(summary)}`;
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
}

function _renderSessionLineage(lineage) {
  if (!Array.isArray(lineage) || !lineage.length) return;
  const { msgEl, bubbleEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("session-history-lineage");
  const items = lineage.map((entry) => {
    const meta = entry.meta_json || {};
    const parts = [];
    if (entry.relationship) parts.push(String(entry.relationship));
    if (meta.archived != null || meta.kept != null) {
      parts.push(`archived ${Number(meta.archived || 0)}`);
      parts.push(`kept ${Number(meta.kept || 0)}`);
    }
    return `
      <div class="session-lineage-item">
        <div class="session-lineage-title">${escHtml(parts.join(" · "))}</div>
        <div class="session-lineage-meta">${escHtml(_fmtHistoryTs(entry.ts) || "unknown time")}</div>
      </div>
    `;
  }).join("");
  bubbleEl.innerHTML = `
    <div class="session-history-label">Lineage</div>
    <div class="session-lineage-list">${items}</div>
  `;
  els.messages.appendChild(msgEl);
}

export function renderSessionHistory(session_id, turns, summary = "", lineage = []) {
  const keepInProgress = isSessionRunning(session_id);
  debugUiLifecycle("render_session_history", {
    session_id,
    running: keepInProgress,
    turns: turns?.length || 0,
  });
  if (!keepInProgress) removeThinkingMsg();
  els.messages.innerHTML = "";
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;

  setCurrentSessionId(session_id);
  _hideSelectionSharePopover();

  _renderSessionSummary(summary);
  _renderSessionLineage(lineage);

  if (!turns.length && !summary && !(lineage || []).length) {
    insertSystemMsg("No history for this session.");
    return;
  }

  for (const t of turns) {
    const ts = t.ts ? new Date(t.ts * 1000) : new Date();
    if (t.role === "user") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdownWithSourceMap(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
      els.messages.appendChild(msgEl);
    } else if (t.role === "assistant") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdownWithSourceMap(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
      els.messages.appendChild(msgEl);
    } else if (t.role === "tool") {
      const el = document.createElement("div");
      renderToolResult(el, t.tool_name || "tool", t.content || "");
      els.messages.appendChild(el);
    }
  }
  if (keepInProgress) rehydrateInProgressUi(session_id);
  scrollToBottom();
}

// ── New session ────────────────────────────────────────────────────────────

export function newSession() {
  resetChatSessionUiState();
  insertSystemMsg("New session started. Use this when you switch to a new topic.");
}

export function resetChatSessionUiState() {
  _hideSelectionSharePopover();
  removeThinkingMsg();
  clearCurrentSessionId();
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  els.messages.innerHTML = "";
  els.tokenStats.textContent   = "";
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
}
