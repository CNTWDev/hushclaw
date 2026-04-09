/**
 * chat.js — Chat message rendering, markdown, thinking indicator, session history.
 */

import {
  state, els, SPINNERS, escHtml, prettyJson, showToast,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { renderMarkdown } from "./markdown.js";

let _spinIdx = 0;
const HTML2CANVAS_URL = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
let _html2canvasLoading = null;

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

function _buildShareCard(bubbleEl, msgEl) {
  // Resolved mode from current theme (always "light" or "dark")
  const mode = document.documentElement.dataset.mode || "dark";
  const datetime = _fmtShareDatetime(msgEl);

  // ── Stage (off-screen) ──────────────────────────────────
  const stage = _mk("div", "cimg-stage");
  const card  = _mk("div", "cimg-card");
  card.dataset.mode = mode;   // drives --ci-* token selection

  // ── TOP WATERMARK BAR ───────────────────────────────────
  //
  //  [3px gradient accent line]
  //  [HC]  HushClaw              2026-04-08 14:32
  //        不知疲倦，默默干活！
  //
  const brandBar   = _mk("div", "cimg-brand-bar");
  const accent     = _mk("div", "cimg-accent");

  const brandInner = _mk("div", "cimg-brand-inner");

  // Left: badge + name + slogan
  const brandLeft  = _mk("div", "cimg-brand-left");
  const brandBadge = _mk("div", "cimg-brand-badge", "HC");
  const brandText  = _mk("div", "cimg-brand-text");
  brandText.appendChild(_mk("div", "cimg-brand-name",   "HushClaw"));
  brandText.appendChild(_mk("div", "cimg-brand-slogan", "不知疲倦，默默干活！"));
  brandLeft.appendChild(brandBadge);
  brandLeft.appendChild(brandText);

  // Right: datetime only
  const brandRight = _mk("div", "cimg-brand-right");
  brandRight.appendChild(_mk("div", "cimg-brand-datetime", datetime));

  brandInner.appendChild(brandLeft);
  brandInner.appendChild(brandRight);
  brandBar.appendChild(accent);
  brandBar.appendChild(brandInner);

  // ── MESSAGE BODY (no role header — brand bar carries identity) ──
  const body    = _mk("div", "cimg-body");
  const content = _mk("div", "cimg-content");
  content.innerHTML = bubbleEl.innerHTML;
  content.querySelectorAll(".msg-actions, .copy-btn, button, .thinking-toggle").forEach(e => e.remove());
  body.appendChild(content);

  card.appendChild(brandBar);
  card.appendChild(body);
  stage.appendChild(card);
  return { stage, card };
}

async function copyBubbleAsImage(bubbleEl, btn) {
  const msgEl = bubbleEl.closest(".msg");
  const { stage, card } = _buildShareCard(bubbleEl, msgEl);
  document.body.appendChild(stage);
  try {
    let blob;
    try {
      blob = await renderNodeToPngBlobWithHtml2Canvas(card);
    } catch {
      // Fallback to SVG-based renderer
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
  const rows = msgs.map(({ role, time, html, isUser }) => `
    <div class="msg ${isUser ? "user" : "ai"}">
      <div class="role">${escHtml(role)}<span class="time">${escHtml(time)}</span></div>
      <div class="body markdown-body">${html}</div>
    </div>`).join("");

  return `<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<title>${escHtml(title)}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "Hiragino Sans GB", "PingFang SC",
                 "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif;
    font-size: 13px; line-height: 1.65; color: #1a1a2e;
    padding: 40px 48px; max-width: 860px; margin: 0 auto;
  }
  h1 { font-size: 18px; font-weight: 700; margin-bottom: 4px; color: #111; }
  .meta { font-size: 11px; color: #888; margin-bottom: 32px; border-bottom: 1px solid #e5e7eb; padding-bottom: 16px; }
  .msg { margin-bottom: 20px; padding: 14px 18px; border-radius: 8px; page-break-inside: avoid; }
  .msg.user { background: #eef2ff; border: 1px solid #c7d2fe; }
  .msg.ai   { background: #f8f9fb; border: 1px solid #e5e7eb; }
  .role {
    font-size: 10.5px; font-weight: 600; color: #6b7280;
    text-transform: uppercase; letter-spacing: 0.05em;
    margin-bottom: 8px; display: flex; justify-content: space-between;
  }
  .time { font-weight: 400; color: #9ca3af; }
  .body { font-size: 13px; }
  .body p { margin: 0 0 8px; }
  .body p:last-child { margin-bottom: 0; }
  .body h1, .body h2, .body h3 { margin: 12px 0 6px; font-weight: 600; }
  .body h1 { font-size: 16px; } .body h2 { font-size: 14px; } .body h3 { font-size: 13px; }
  .body ul, .body ol { padding-left: 20px; margin: 6px 0; }
  .body li { margin: 2px 0; }
  .body pre { background: #f3f4f6; border-radius: 6px; padding: 12px 14px; overflow-x: auto; margin: 8px 0; }
  .body code { font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace; font-size: 12px; }
  .body p code, .body li code { background: #f3f4f6; border-radius: 3px; padding: 1px 5px; }
  .body table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  .body th, .body td { border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; font-size: 12px; }
  .body th { background: #f3f4f6; font-weight: 600; }
  .body blockquote { border-left: 3px solid #c7d2fe; margin: 8px 0; padding: 4px 12px; color: #6b7280; }
  .body hr { border: none; border-top: 1px solid #e5e7eb; margin: 12px 0; }
  .body a { color: #5b67f6; text-decoration: none; }
  @page { margin: 18mm 20mm; }
  @media print { body { padding: 0; } }
</style>
</head><body>
<h1>${escHtml(title)}</h1>
<p class="meta">Generated at ${new Date().toLocaleString()}</p>
${rows}
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
  const role = _roleLabelFromMsg(msgEl);
  const time = msgEl.querySelector(".msg-time")?.textContent?.trim() || fmtTime(new Date());
  const html = bubbleEl?.innerHTML ?? "";
  const isUser = msgEl.classList.contains("user");
  _printMessages([{ role, time, html, isUser }], `${role} Message`);
  setCopyBtnTempText(btn, "Opened", btn.innerHTML || "Print");
}

function addCopyActions(msgEl, bubbleEl, contentEl, ts) {
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
  mdBtn.title = "Copy original Markdown source text";
  mdBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const raw = bubbleEl._raw ?? bubbleEl.textContent ?? "";
    try {
      await navigator.clipboard.writeText(raw);
      setCopyBtnTempText(mdBtn, "✓ Copied", `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Copy`);
    } catch {
      setCopyBtnTempText(mdBtn, "Failed", `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Copy`);
    }
  });

  const imgBtn = document.createElement("button");
  imgBtn.type = "button";
  imgBtn.className = "msg-copy-btn";
  imgBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="10" height="10" rx="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="4" r="1" fill="currentColor"/><path d="M1 8.5l3-3 2.5 2.5 1.5-2 2.5 2.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg> Image`;
  imgBtn._origHtml = imgBtn.innerHTML;
  imgBtn.title = "Copy message as image to clipboard";
  imgBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      await copyBubbleAsImage(bubbleEl, imgBtn);
    } catch (err) {
      setCopyBtnTempText(imgBtn, "Failed", `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="10" height="10" rx="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="4" r="1" fill="currentColor"/><path d="M1 8.5l3-3 2.5 2.5 1.5-2 2.5 2.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg> Image`);
      showToast(getCopyImageErrorMessage(err), "error");
    }
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
    shareBtn.title = "Share this Q&A to Community Forum";
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
  bubbleEl.innerHTML = renderMarkdown(text);
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

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
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
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
  state._aiBubbleEl.innerHTML = renderMarkdown(text);
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
  el.className     = isError ? "tool-line has-error" : "tool-line has-result";

  if (isDevMode()) {
    const statusIcon = isError
      ? `<span class="tl-err">✗</span>`
      : `<span class="tl-done">✓</span>`;
    el.innerHTML = `<span class="tl-name">⚙ ${escHtml(toolName)}</span>`
                 + `<span class="tl-result">${escHtml(preview)}</span>`
                 + statusIcon
                 + (expandable ? `<span class="tl-expand">›</span><div class="tl-body">${escHtml(raw)}</div>` : "");
    if (expandable) {
      el.addEventListener("click", () => el.classList.toggle("expanded"));
    }
  } else {
    const lbl  = _toolLabel(toolName);
    const text = isError ? lbl.error : lbl.done;
    const errMark = isError ? ` <span class="tl-err">✗</span>` : "";
    const detailHtml = expandable
      ? `<span class="tl-detail-btn" role="button" tabindex="0">· 详情</span><div class="tl-body">${escHtml(raw)}</div>`
      : "";
    el.innerHTML = `<span class="tl-label">${lbl.icon} ${escHtml(text)}</span>`
                 + errMark
                 + detailHtml;
    if (expandable) {
      const btn = el.querySelector(".tl-detail-btn");
      if (btn) {
        const toggle = () => {
          el.classList.toggle("expanded");
          btn.textContent = el.classList.contains("expanded") ? "· 收起" : "· 详情";
        };
        btn.addEventListener("click",   toggle);
        btn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") toggle(); });
      }
    }
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

export function renderSessionHistory(session_id, turns) {
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

  if (!turns.length) {
    insertSystemMsg("No history for this session.");
    return;
  }

  for (const t of turns) {
    const ts = t.ts ? new Date(t.ts * 1000) : new Date();
    if (t.role === "user") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
      els.messages.appendChild(msgEl);
    } else if (t.role === "assistant") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
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
