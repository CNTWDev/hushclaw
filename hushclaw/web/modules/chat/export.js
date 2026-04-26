/**
 * chat/export.js — Message copy actions, image export, PDF print, share-to-forum.
 *
 * Extracted from chat.js. Imports only from state.js, markdown.js, modal.js —
 * no dependency on ../chat.js, so no circular imports.
 */

import { showToast, escHtml, els } from "../state.js";
import { renderMarkdown } from "../markdown.js";
import { openDialog, closeModal } from "../modal.js";

const HTML2CANVAS_URL = "/html2canvas.min.js";
const HTML2CANVAS_CDN = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
let _html2canvasLoading = null;

const SHARE_EXPORT_PRESET = Object.freeze({
  width: 900,        // logical CSS px — outputs 1800px at 2x (Instagram/WeChat standard)
  minHeight: 1260,
  maxWidthPx: 2520,
  maxHeightPx: 3880,
  maxPixels: 12_000_000,
  preferredScale: 2.5,
  preferredScaleCompact: 2.2,
  minScale: 1.3,
});

// ── Misc helpers ───────────────────────────────────────────────────────────

export function setCopyBtnTempText(btn, html, fallbackHtml) {
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

function _mk(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
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

// ── PNG rendering ──────────────────────────────────────────────────────────

// SVG foreignObject drawn into canvas ALWAYS taints the canvas in Chrome
// (https://bugs.chromium.org/p/chromium/issues/detail?id=294129 — open since 2013).
// Use this only as a last-resort fallback that downloads an SVG file instead of PNG.
async function renderNodeToSvgBlob(node) {
  await _waitForShareCardAssets(node);
  const rect = node.getBoundingClientRect();
  const width  = Math.max(1, Math.ceil(rect.width  || node.scrollWidth  || 720));
  const height = Math.max(1, Math.ceil(rect.height || node.scrollHeight || 200));
  console.debug("[export] SVG fallback: node size", width, "x", height);

  // Inline all CSS (minus @font-face) so the SVG renders standalone
  let inlineCSS = "";
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        if (rule.type !== CSSRule.FONT_FACE_RULE) inlineCSS += rule.cssText + "\n";
      }
    } catch { /* cross-origin sheet — skip */ }
  }

  const cloned = node.cloneNode(true);
  const styleEl = document.createElement("style");
  styleEl.textContent = inlineCSS;
  cloned.prepend(styleEl);
  cloned.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  const xhtml = new XMLSerializer().serializeToString(cloned);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><foreignObject width="100%" height="100%">${xhtml}</foreignObject></svg>`;
  return new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
}

async function ensureHtml2Canvas() {
  if (window.html2canvas) return window.html2canvas;
  if (_html2canvasLoading) return _html2canvasLoading;
  _html2canvasLoading = _loadScript(HTML2CANVAS_URL)
    .catch(() => _loadScript(HTML2CANVAS_CDN))
    .then(() => {
      if (!window.html2canvas) throw new Error("html2canvas loaded but unavailable");
      return window.html2canvas;
    })
    .catch((err) => {
      _html2canvasLoading = null;
      throw err;
    });
  return _html2canvasLoading;
}

function _loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Failed to load html2canvas from ${src}`));
    document.head.appendChild(s);
  });
}

// Convert color(srgb r g b) / color(srgb r g b / a) → rgb()/rgba()
// Chrome emits this format in getComputedStyle for oklch/oklab/lch/lab colors.
// html2canvas 1.4.1 cannot parse the "color()" function and throws.
function _fixColorFn(v) {
  return v.replace(
    /color\(\s*(?:srgb|display-p3)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)(?:\s*\/\s*([\d.e+-]+))?\s*\)/gi,
    (_, r, g, b, a) => {
      const ri = Math.min(255, Math.max(0, Math.round(+r * 255)));
      const gi = Math.min(255, Math.max(0, Math.round(+g * 255)));
      const bi = Math.min(255, Math.max(0, Math.round(+b * 255)));
      return a != null
        ? `rgba(${ri},${gi},${bi},${(+a).toFixed(3)})`
        : `rgb(${ri},${gi},${bi})`;
    }
  );
}

// Walk every element in the cloned doc and rewrite color() in computed styles
// as inline style overrides so html2canvas never encounters the unsupported syntax.
function _sanitizeColorValuesForH2C(clonedDoc) {
  const COLOR_PROPS = [
    "color", "background-color",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "outline-color", "text-decoration-color", "caret-color", "column-rule-color",
    "box-shadow", "fill", "stroke",
  ];
  const els = clonedDoc.querySelectorAll("*");
  els.forEach(el => {
    try {
      const cs = window.getComputedStyle(el);
      for (const prop of COLOR_PROPS) {
        const val = cs.getPropertyValue(prop);
        if (val && val.includes("color(")) {
          el.style.setProperty(prop, _fixColorFn(val), "important");
        }
      }
    } catch { /* ignore */ }
  });
}

async function renderNodeToPngBlobWithHtml2Canvas(node) {
  console.debug("[export] loading html2canvas …");
  const html2canvas = await ensureHtml2Canvas();
  console.debug("[export] html2canvas loaded, rendering …");
  await _waitForShareCardAssets(node);
  const isLight = node.dataset?.mode === "light";
  const bgColor = node.classList.contains("cimg-card")
    ? (isLight ? "#f8f9fc" : "#14161f")
    : null;
  const rect = node.getBoundingClientRect();
  const width  = Math.max(1, Math.ceil(rect.width  || node.scrollWidth  || 720));
  const height = Math.max(1, Math.ceil(rect.height || node.scrollHeight || 200));
  const preferredScale = Number(node.dataset?.exportScale || SHARE_EXPORT_PRESET.preferredScale);
  const scale = _getSafeRenderScale(width, height, preferredScale);
  console.debug("[export] h2c: node size", width, "x", height, "scale", scale, "bg", bgColor);
  const canvas = await html2canvas(node, {
    backgroundColor: bgColor,
    scale,
    useCORS: true,
    logging: false,
    allowTaint: false,
    onclone: (clonedDoc) => {
      // Rewrite color(srgb ...) computed values to rgb() across the entire
      // cloned document so html2canvas 1.4.1 can parse all color properties.
      _sanitizeColorValuesForH2C(clonedDoc);
    },
  });
  console.debug("[export] h2c: canvas", canvas.width, "x", canvas.height);
  return await new Promise((resolve, reject) => {
    try {
      canvas.toBlob((png) => {
        if (png) { console.debug("[export] h2c toBlob OK", png.size, "bytes"); resolve(png); }
        else { console.error("[export] h2c toBlob returned null"); reject(new Error("PNG encoding failed")); }
      }, "image/png");
    } catch (e) {
      console.error("[export] h2c toBlob threw", e);
      reject(e);
    }
  });
}

function _getSafeRenderScale(width, height, preferredScale = 1.6) {
  const widthLimit = SHARE_EXPORT_PRESET.maxWidthPx / Math.max(1, width);
  const heightLimit = SHARE_EXPORT_PRESET.maxHeightPx / Math.max(1, height);
  const pixelLimit = Math.sqrt(SHARE_EXPORT_PRESET.maxPixels / Math.max(1, width * height));
  const safeScale = Math.min(preferredScale, widthLimit, heightLimit, pixelLimit);
  return Math.max(SHARE_EXPORT_PRESET.minScale, Number.isFinite(safeScale) ? safeScale : 1);
}

function _applyShareExportPreset(card, bubbleEl) {
  const text = (bubbleEl?._raw ?? bubbleEl?.innerText ?? bubbleEl?.textContent ?? "").trim();
  const compact = text.length > 2200;
  const width      = compact ? 860  : SHARE_EXPORT_PRESET.width;
  const minHeight  = compact ? 1200 : SHARE_EXPORT_PRESET.minHeight;
  const bodyPadX   = compact ? 80   : 88;
  const bodyPadTop = compact ? 88   : 96;
  const footerPadX = compact ? 80   : 88;

  card.style.setProperty("--ci-paper-width", `${width}px`);
  card.style.setProperty("--ci-paper-min-height", `${minHeight}px`);
  card.style.setProperty("--ci-body-pad-x", `${bodyPadX}px`);
  card.style.setProperty("--ci-body-pad-top", `${bodyPadTop}px`);
  card.style.setProperty("--ci-footer-pad-x", `${footerPadX}px`);
  card.dataset.exportScale = String(compact ? SHARE_EXPORT_PRESET.preferredScaleCompact : SHARE_EXPORT_PRESET.preferredScale);
}

async function _waitForShareCardAssets(node) {
  if (document.fonts?.ready) {
    try { await document.fonts.ready; } catch {}
  }
  const images = Array.from(node.querySelectorAll("img"));
  if (images.length) {
    await Promise.all(images.map((img) => new Promise((resolve) => {
      if (img.complete && img.naturalWidth !== 0) {
        resolve();
        return;
      }
      const done = () => {
        img.removeEventListener("load", done);
        img.removeEventListener("error", done);
        resolve();
      };
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    })));
  }
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
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

// ── Share card helpers ─────────────────────────────────────────────────────

function _fmtShareDatetime(msgEl) {
  const timeEl  = msgEl?.querySelector(".msg-time");
  const timeTxt = timeEl?.textContent?.trim() || "";
  const now     = new Date();
  const yyyy    = now.getFullYear();
  const mm      = String(now.getMonth() + 1).padStart(2, "0");
  const dd      = String(now.getDate()).padStart(2, "0");
  const today   = `${yyyy}-${mm}-${dd}`;
  if (timeTxt.includes("-")) return `${yyyy}-${timeTxt}`;
  if (timeTxt) return `${today} ${timeTxt}`;
  const hhmm = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${today} ${hhmm}`;
}

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

function _buildTemplatePickerHtml() {
  return `<div class="img-tpl-gallery">
    <div class="img-tpl-intro">
      <div class="img-tpl-kicker">Share Image Studio</div>
      <p class="img-tpl-note">Choose a template by content type — not just color. Reading templates are minimal, code templates are crisp, data templates are structured. Each comes in Light and Dark.</p>
    </div>
    <div class="img-tpl-picker">
      <button class="img-tpl-opt" data-tpl="reading-dark" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--reading-dark"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Reading · Dark</div>
            <span class="img-tpl-chip img-tpl-chip--dark">Dark</span>
          </div>
          <div class="img-tpl-subtitle">Reading Dark</div>
          <div class="img-tpl-desc">Dark long-form template for summaries, analysis, tutorials, and opinion pieces. Text-first layout.</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="reading-light" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--reading-light"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Reading · Light</div>
            <span class="img-tpl-chip img-tpl-chip--light">Light</span>
          </div>
          <div class="img-tpl-subtitle">Reading Light</div>
          <div class="img-tpl-desc">The closest to paper and magazine pages — clean, bright, ideal for formal sharing and print-style content.</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="code-dark" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--code-dark"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Code · Dark</div>
            <span class="img-tpl-chip img-tpl-chip--dark">Dark</span>
          </div>
          <div class="img-tpl-subtitle">Code Dark</div>
          <div class="img-tpl-desc">For code blocks, command lines, and config snippets. Sharper hierarchy, stronger technical feel.</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="code-light" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--code-light"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Code · Light</div>
            <span class="img-tpl-chip img-tpl-chip--light">Light</span>
          </div>
          <div class="img-tpl-subtitle">Code Light</div>
          <div class="img-tpl-desc">Apple developer docs aesthetic — code, commands, and comments stay crisp and legible on white.</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="data-dark" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--data-dark"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Data · Dark</div>
            <span class="img-tpl-chip img-tpl-chip--dark">Dark</span>
          </div>
          <div class="img-tpl-subtitle">Data Dark</div>
          <div class="img-tpl-desc">For tables, charts, and structured conclusions — higher information density while staying clean.</div>
        </div>
      </button>
      <button class="img-tpl-opt" data-tpl="data-light" type="button">
        <div class="img-tpl-thumb img-tpl-thumb--data-light"></div>
        <div class="img-tpl-meta">
          <div class="img-tpl-name-row">
            <div class="img-tpl-label">Data · Light</div>
            <span class="img-tpl-chip img-tpl-chip--light">Light</span>
          </div>
          <div class="img-tpl-subtitle">Data Light</div>
          <div class="img-tpl-desc">Bright information-page style — tables and charts are most stable here; numbers and structure stand out.</div>
        </div>
      </button>
    </div>
  </div>`;
}

function _detectShareTemplate(bubbleEl, themeMode) {
  const hasCode = !!bubbleEl.querySelector("pre, code");
  const hasData = !!bubbleEl.querySelector("table, .html-inline-preview, iframe, canvas, svg");
  if (hasData) return themeMode === "light" ? "data-light" : "data-dark";
  if (hasCode) return themeMode === "light" ? "code-light" : "code-dark";
  return themeMode === "light" ? "reading-light" : "reading-dark";
}

function _buildShareMarkdown(bubbleEl, msgEl) {
  const aiText   = (bubbleEl._raw ?? bubbleEl.textContent ?? "").trim();
  const datetime = _fmtShareDatetime(msgEl);
  const userText = _getPrevUserText(msgEl);
  const lines    = [];

  if (userText) {
    lines.push(`> 💬 **Question**`);
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

function _buildShareCard(bubbleEl, msgEl, template = "auto") {
  const themeMode = document.documentElement.dataset.mode || "dark";
  const datetime  = _fmtShareDatetime(msgEl);

  let normalizedTemplate = template;
  if (normalizedTemplate === "auto") normalizedTemplate = _detectShareTemplate(bubbleEl, themeMode);

  const cardMode = normalizedTemplate.endsWith("-light") ? "light" : "dark";
  const cardTemplate = normalizedTemplate;
  const scenario = normalizedTemplate.split("-")[0];
  const scenarioLabel = scenario === "code"
    ? "Code Sheet"
    : scenario === "data"
      ? "Data Sheet"
      : "Reading Sheet";
  const scenarioSub = scenario === "code"
    ? "Code-first export"
    : scenario === "data"
      ? "Chart / table export"
      : "Editorial long-form export";

  const stage = _mk("div", "cimg-stage");
  const card  = _mk("div", "cimg-card");
  card.dataset.mode     = cardMode;
  card.dataset.template = cardTemplate;
  card.dataset.scenario = scenario;
  _applyShareExportPreset(card, bubbleEl);

  const deco = _mk("div", "cimg-deco-quote");
  deco.textContent = "❝";
  card.appendChild(deco);

  const brandBar = _mk("div", "cimg-brand-bar");
  brandBar.innerHTML = `
    <div class="cimg-accent"></div>
    <div class="cimg-brand-inner">
      <div class="cimg-brand-left">
        <div class="cimg-brand-badge">HC</div>
        <div class="cimg-brand-text">
          <div class="cimg-brand-name">HushClaw ${scenarioLabel}</div>
          <div class="cimg-brand-slogan">${scenarioSub}</div>
        </div>
      </div>
      <div class="cimg-brand-right">
        <div class="cimg-brand-datetime">${escHtml(datetime)}</div>
        <div class="cimg-brand-attr">Assistant Response</div>
      </div>
    </div>
  `;
  card.appendChild(brandBar);

  const body    = _mk("div", "cimg-body");
  const content = _mk("div", "cimg-content");
  content.innerHTML = bubbleEl.innerHTML;
  content.querySelectorAll(".msg-actions, .copy-btn, button, .thinking-toggle, .msg-actions-footer").forEach(e => e.remove());
  body.appendChild(content);
  card.appendChild(body);

  const footer = _mk("div", "cimg-footer");

  const fLeft = _mk("div", "cimg-footer-left");
  const avatar = _mk("div", "cimg-footer-avatar");
  avatar.textContent = "HC";
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
    if (navigator.clipboard?.write && window.ClipboardItem) {
      // Pass Promise directly so clipboard.write is initiated inside the user gesture context.
      // Awaiting after the async render would break the gesture chain and cause NotAllowedError.
      const blobPromise = renderNodeToPngBlobWithHtml2Canvas(card);
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise })]);
      setCopyBtnTempText(btn, "✓ Copied", btn._origHtml || btn.innerHTML);
      return;
    }
    const blob = await renderNodeToPngBlobWithHtml2Canvas(card);
    downloadBlob(blob, "hushclaw-message.png");
    setCopyBtnTempText(btn, "Saved", btn._origHtml || btn.innerHTML);
    showToast("Clipboard image not supported. Downloaded PNG instead.", "warn");
  } finally {
    stage.remove();
  }
}

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
    title: "Choose share style",
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

// ── PDF / print export ─────────────────────────────────────────────────────

function _buildPrintHtml(msgs, title = "HushClaw Chat Export") {
  const now = new Date();
  const generatedAt = now.toLocaleString([], {
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
<html lang="en">
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

function _shareToForum(msgEl, bubbleEl, btn) {
  const aiText   = (bubbleEl._raw ?? bubbleEl.innerText ?? "").trim();
  let userText   = "";
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
    ? `**Question:**\n\n${userText}\n\n---\n\n**Response:**\n\n${aiText}`
    : aiText;

  import("../../transsion/forum.js")
    .then(({ openComposeWith }) => {
      import("../panels.js").then(({ switchTab }) => {
        switchTab("forum");
        requestAnimationFrame(() => openComposeWith(title, content));
      });
    })
    .catch(() => {
      import("../state.js").then(({ showToast: _showToast }) =>
        _showToast("Community forum plugin not loaded. Please sign in to your Transsion account.", "warn")
      );
    });

  setCopyBtnTempText(btn, "Shared ✓", btn.innerHTML);
}

// ── Copy actions toolbar ───────────────────────────────────────────────────

export function addCopyActions(msgEl, bubbleEl, contentEl, ts) {
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

  if (msgEl.dataset.role === "ai") {
    const shareBtn = document.createElement("button");
    shareBtn.type = "button";
    shareBtn.className = "msg-copy-btn share-forum-btn";
    shareBtn.title = "Share this Q&A to Knowledge";
    shareBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><circle cx="9" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="9" cy="9" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="3" cy="6" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M4.4 6.7 7.6 8.3M7.6 3.7 4.4 5.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Share`;
    shareBtn.style.display = "none";
    shareBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _shareToForum(msgEl, bubbleEl, shareBtn);
    });
    actions.appendChild(shareBtn);

    if (document.querySelector('nav.tabs [data-tab="forum"]')?.style.display !== "none"
        && document.querySelector('nav.tabs [data-tab="forum"]')) {
      shareBtn.style.display = "";
    }
  }

  footer.appendChild(timeEl);
  footer.appendChild(actions);
  contentEl.appendChild(footer);
}

// ── Full session PDF export ────────────────────────────────────────────────

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
