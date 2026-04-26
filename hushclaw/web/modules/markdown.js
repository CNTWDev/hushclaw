/**
 * markdown.js — Safe markdown renderer for chat bubbles.
 * Keeps output HTML sanitized by escaping first, then controlled replacements.
 */

import { escHtml } from "./state.js";
import { resolveFileUrl } from "./http.js";

// ── HTML block inline preview store ──────────────────────────────────────────
const _htmlBlockStore = new Map(); // key → raw HTML string

function _htmlBlockKey(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  return "h" + (h >>> 0).toString(36);
}

export function getHtmlBlock(key) { return _htmlBlockStore.get(key) ?? null; }

const FILES_PATH_PATTERN = "\\/files\\/(?:artifacts\\/[\\w.\\-]+(?:\\/[\\w.\\-/]+)?\\/?|[\\w.\\-]+)";
const STRUCTURED_DOWNLOAD_RE = new RegExp(`^${FILES_PATH_PATTERN}(?:\\?[^\\s<)]*)?$`);
const PLAIN_DOWNLOAD_RE = new RegExp(`(^|[\\s(])(${FILES_PATH_PATTERN})(\\?[^\\s<)]*)?(?=$|[\\s<)])`, "g");
const ABS_DOWNLOAD_RE = new RegExp(`(^|[\\s(])(https?:\\/\\/[^\\s<)]+(?:${FILES_PATH_PATTERN})(?:\\?[^\\s<)]*)?)(?=$|[\\s<)])`, "g");

const _INLINE_EXTS = new Set([".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".webm", ".ogg", ".wav"]);
function _isInline(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && _INLINE_EXTS.has(name.slice(dot).toLowerCase());
}
function _dlLink(href, name) {
  const safe = escHtml(name);
  if (_isInline(name)) {
    return `<a class="dl-link" href="${href}" target="_blank" rel="noopener">⬇ ${safe}</a>`;
  }
  return `<a class="dl-link" href="${href}" download="${safe}">⬇ ${safe}</a>`;
}

function highlightCode(code, lang = "") {
  const language = String(lang || "").toLowerCase();
  const tokens = [];
  const take = (html) => {
    const idx = tokens.length;
    tokens.push(html);
    return `@@CODETOK_${idx}@@`;
  };

  let out = code;
  out = out.replace(/(\/\*[\s\S]*?\*\/|\/\/[^\n]*)/g, (m) => take(`<span class="tok-c">${m}</span>`));
  out = out.replace(/(&quot;[\s\S]*?&quot;|'[^'\n]*')/g, (m) => take(`<span class="tok-s">${m}</span>`));

  const langKeywords = {
    js: ["const", "let", "var", "function", "return", "if", "else", "for", "while", "class", "new", "try", "catch", "throw", "import", "from", "export", "async", "await", "true", "false", "null", "undefined"],
    javascript: ["const", "let", "var", "function", "return", "if", "else", "for", "while", "class", "new", "try", "catch", "throw", "import", "from", "export", "async", "await", "true", "false", "null", "undefined"],
    ts: ["const", "let", "var", "function", "return", "if", "else", "for", "while", "class", "new", "try", "catch", "throw", "import", "from", "export", "async", "await", "interface", "type", "extends", "implements", "true", "false", "null", "undefined"],
    typescript: ["const", "let", "var", "function", "return", "if", "else", "for", "while", "class", "new", "try", "catch", "throw", "import", "from", "export", "async", "await", "interface", "type", "extends", "implements", "true", "false", "null", "undefined"],
    py: ["def", "class", "return", "if", "elif", "else", "for", "while", "try", "except", "finally", "raise", "import", "from", "as", "with", "lambda", "True", "False", "None", "async", "await"],
    python: ["def", "class", "return", "if", "elif", "else", "for", "while", "try", "except", "finally", "raise", "import", "from", "as", "with", "lambda", "True", "False", "None", "async", "await"],
    json: ["true", "false", "null"],
    sh: ["if", "then", "else", "fi", "for", "do", "done", "while", "function", "case", "esac", "export", "local"],
    bash: ["if", "then", "else", "fi", "for", "do", "done", "while", "function", "case", "esac", "export", "local"],
    sql: ["select", "from", "where", "join", "left", "right", "inner", "outer", "on", "group", "by", "order", "insert", "into", "update", "delete", "create", "drop", "alter", "limit", "and", "or", "not", "null"],
  };

  const keys = langKeywords[language] || [];
  if (keys.length) {
    const kw = new RegExp(`\\b(?:${keys.join("|")})\\b`, "g");
    out = out.replace(kw, (m) => take(`<span class="tok-k">${m}</span>`));
  }

  out = out.replace(/\b\d+(?:\.\d+)?\b/g, (m) => take(`<span class="tok-n">${m}</span>`));
  out = out.replace(/\$[A-Za-z_][A-Za-z0-9_]*/g, (m) => take(`<span class="tok-v">${m}</span>`));

  out = out.replace(/@@CODETOK_(\d+)@@/g, (_m, i) => tokens[Number(i)] || "");
  return out;
}

export function renderMarkdown(raw) {
  const rawText = String(raw).replace(/^\n+/, "").replace(/\n+$/, "");
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  const trustedOrigins = new Set([location.origin]);
  const publicBase = String(window.__HUSHCLAW_PUBLIC_BASE_URL || "").trim();
  if (publicBase) {
    try {
      trustedOrigins.add(new URL(publicBase, location.origin).origin);
    } catch (_e) {
      // Ignore invalid public base URL.
    }
  }

  // Structured artifact payload fast-path.
  // Example: {"trusted":true,"url":"/files/artifacts/abc123/report.pdf","name":"report.pdf","artifact_id":"abc123"}
  try {
    const parsed = JSON.parse(rawText.trim());
    const note = (parsed && typeof parsed === "object" && typeof parsed.message === "string")
      ? parsed.message.trim()
      : "";
    const metas = [];
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.artifacts)) {
      for (const item of parsed.artifacts) {
        if (item && typeof item === "object") metas.push(item);
      }
    } else {
      const meta = (parsed && typeof parsed === "object" && parsed.artifact && typeof parsed.artifact === "object")
        ? parsed.artifact
        : parsed;
      if (meta && typeof meta === "object") metas.push(meta);
    }
    const links = metas
      .filter((meta) => typeof meta.url === "string" && STRUCTURED_DOWNLOAD_RE.test(meta.url))
      .map((meta) => {
        const name = String(meta.name || meta.url.split("/").pop() || "file");
        const href = resolveFileUrl(meta.url, apiKey);
        return _dlLink(href, name);
      });
    if (links.length) {
      return note
        ? `<div class="dl-note">${escHtml(note)}</div>${links.join("<br>")}`
        : links.join("<br>");
    }
  } catch (_e) {
    // Not JSON payload; continue markdown rendering.
  }

  // Extract complete ```html blocks BEFORE HTML-escaping so we preserve raw HTML content.
  // Placeholders contain only alphanumeric chars — they survive escHtml unchanged.
  let normalized = rawText.replace(/\r\n?/g, "\n");
  // Complete blocks (with closing fence).
  normalized = normalized.replace(/```html\n([\s\S]*?)```/g, (_m, inner) => {
    const key = _htmlBlockKey(inner.trim());
    _htmlBlockStore.set(key, inner.trim());
    return `@@HTML_BLOCK_${key}@@`;
  });
  // Trailing partial block (during streaming, closing fence may not have arrived yet).
  normalized = normalized.replace(/```html\n([\s\S]+)$/, (_m, inner) => {
    const trimmed = inner.trim();
    if (!trimmed) return _m;
    const key = _htmlBlockKey(trimmed);
    _htmlBlockStore.set(key, trimmed);
    return `@@HTML_BLOCK_${key}@@`;
  });
  let s = escHtml(normalized);

  const fenced = [];
  s = s.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_m, lang, inner) => {
    const i = fenced.length;
    const langNorm = String(lang || "").toLowerCase();
    const cls = langNorm ? ` class="lang-${langNorm}"` : "";
    const dataLang = langNorm ? ` data-lang="${langNorm}"` : "";
    const highlighted = highlightCode(inner, langNorm);
    fenced.push(`<pre class="code-block"${dataLang}><code${cls}>${highlighted}</code></pre>`);
    return `@@FENCED_${i}@@`;
  });

  const inlineCodes = [];
  s = s.replace(/`([^`]+)`/g, (_m, inner) => {
    const i = inlineCodes.length;
    inlineCodes.push(`<code>${inner}</code>`);
    return `@@INLINE_${i}@@`;
  });

  s = s.replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|\/files\/)[^\s)]+)\)/g, (_m, label, href) => {
    // Relative /files/ path — render as download link.
    if (href.startsWith("/files/")) {
      const hrefWithKey = resolveFileUrl(href, apiKey);
      return _dlLink(hrefWithKey, label);
    }
    // Guard absolute links that target /files on untrusted domains.
    try {
      const u = new URL(href);
      if (u.pathname.startsWith("/files/") && !trustedOrigins.has(u.origin)) {
        return `<span class="dl-link untrusted" title="Untrusted download domain">${label}</span>`;
      }
    } catch (_e) {
      // Keep default link rendering for malformed URLs.
    }
    return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
  });

  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

  s = s.replace(/^######[ \t]+(.+)$/gm, "<h6>$1</h6>");
  s = s.replace(/^#####[ \t]+(.+)$/gm,  "<h6>$1</h6>");
  s = s.replace(/^####[ \t]+(.+)$/gm,   "<h6>$1</h6>");
  s = s.replace(/^###[ \t]+(.+)$/gm,    "<h5>$1</h5>");
  s = s.replace(/^##[ \t]+(.+)$/gm,     "<h4>$1</h4>");
  s = s.replace(/^#[ \t]+(.+)$/gm,      "<h3>$1</h3>");
  s = s.replace(/^>[ \t]?(.+)$/gm, "<blockquote>$1</blockquote>");
  s = s.replace(/^(---|\*\*\*|___)$/gm, "<hr>");

  function splitCells(line) {
    const cells = line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|");
    return cells.map((c) => c.trim());
  }

  function isTableSep(line) {
    const cells = splitCells(line);
    return cells.length > 0 && cells.every((c) => /^:?-{3,}:?$/.test(c));
  }

  function toIndentCount(indentRaw) {
    return indentRaw.replace(/\t/g, "  ").length;
  }

  const tableLines = s.split("\n");
  const tableOut = [];
  for (let i = 0; i < tableLines.length; i++) {
    const header = tableLines[i];
    const sep = tableLines[i + 1];
    if (header && sep && header.includes("|") && isTableSep(sep)) {
      const headers = splitCells(header);
      const aligns = splitCells(sep).map((c) => {
        const left = c.startsWith(":");
        const right = c.endsWith(":");
        if (left && right) return "center";
        if (right) return "right";
        return "left";
      });

      const rows = [];
      i += 2;
      while (i < tableLines.length && tableLines[i].includes("|") && tableLines[i].trim()) {
        rows.push(splitCells(tableLines[i]));
        i++;
      }
      i -= 1;

      const thead = headers.map((h, idx) => `<th style="text-align:${aligns[idx] || "left"}">${h}</th>`).join("");
      const tbody = rows.map((row) => {
        const tds = row.map((c, idx) => `<td style="text-align:${aligns[idx] || "left"}">${c}</td>`).join("");
        return `<tr>${tds}</tr>`;
      }).join("");
      tableOut.push(`<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
      continue;
    }
    tableOut.push(header);
  }
  s = tableOut.join("\n");

  const listLines = s.split("\n");
  const listOut = [];
  const stack = [];
  for (const line of listLines) {
    const m = line.match(/^([ \t]*)([-*+]|\d+\.)[ \t]+(.+)$/);
    if (!m) {
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top.liOpen) { listOut.push("</li>"); top.liOpen = false; }
        listOut.push(`</${top.type}>`);
        stack.pop();
      }
      listOut.push(line);
      continue;
    }

    const indent = toIndentCount(m[1]);
    const type = /\d+\./.test(m[2]) ? "ol" : "ul";
    let content = m[3];
    let task = null;
    const taskMatch = content.match(/^\[( |x|X)\][ \t]+(.+)$/);
    if (taskMatch) {
      task = taskMatch[1].toLowerCase() === "x";
      content = taskMatch[2];
    }

    while (stack.length && indent < stack[stack.length - 1].indent) {
      const top = stack[stack.length - 1];
      if (top.liOpen) { listOut.push("</li>"); top.liOpen = false; }
      listOut.push(`</${top.type}>`);
      stack.pop();
    }

    if (!stack.length || indent > stack[stack.length - 1].indent) {
      listOut.push(`<${type}>`);
      stack.push({ type, indent, liOpen: false });
    } else if (stack[stack.length - 1].type !== type) {
      const top = stack[stack.length - 1];
      if (top.liOpen) { listOut.push("</li>"); top.liOpen = false; }
      listOut.push(`</${top.type}>`);
      stack.pop();
      listOut.push(`<${type}>`);
      stack.push({ type, indent, liOpen: false });
    } else if (stack[stack.length - 1].liOpen) {
      listOut.push("</li>");
      stack[stack.length - 1].liOpen = false;
    }

    if (task !== null) {
      listOut.push(`<li class="task-item"><input type="checkbox" disabled ${task ? "checked" : ""}> <span>${content}</span>`);
    } else {
      listOut.push(`<li>${content}`);
    }
    stack[stack.length - 1].liOpen = true;
  }
  while (stack.length) {
    const top = stack[stack.length - 1];
    if (top.liOpen) listOut.push("</li>");
    listOut.push(`</${top.type}>`);
    stack.pop();
  }
  s = listOut.join("\n");

  // Auto-link plain /files/<id_name> tokens only in text segments (not inside tags/attrs).
  const linkifyFiles = (text) => {
    let out = text.replace(PLAIN_DOWNLOAD_RE, (_m, prefix, fid, query) => {
      const rawHref = `${fid}${query || ""}`;
      const href = resolveFileUrl(rawHref, apiKey);
      const leaf = fid.split("/").filter(Boolean).pop() || "file";
      const name = leaf.includes("_") ? leaf.split("_").slice(1).join("_") : leaf;
      return `${prefix}${_dlLink(href, name)}`;
    });
    out = out.replace(ABS_DOWNLOAD_RE, (_m, prefix, absUrl) => {
      try {
        const u = new URL(absUrl);
        if (!trustedOrigins.has(u.origin)) {
          return `${prefix}<span class="dl-link untrusted" title="Untrusted download domain">${escHtml(absUrl)}</span>`;
        }
        const href = resolveFileUrl(absUrl, apiKey);
        const fid = (u.pathname.split("/").pop() || "file");
        const name = fid.includes("_") ? fid.split("_").slice(1).join("_") : fid;
        return `${prefix}${_dlLink(href, name)}`;
      } catch (_e) {
        return `${prefix}${absUrl}`;
      }
    });
    return out;
  };
  const chunks = [];
  let cursor = 0;
  while (cursor < s.length) {
    const lt = s.indexOf("<", cursor);
    if (lt === -1) {
      chunks.push(linkifyFiles(s.slice(cursor)));
      break;
    }
    chunks.push(linkifyFiles(s.slice(cursor, lt)));
    const gt = s.indexOf(">", lt);
    if (gt === -1) {
      chunks.push(s.slice(lt));
      break;
    }
    chunks.push(s.slice(lt, gt + 1));
    cursor = gt + 1;
  }
  s = chunks.join("");

  // For markdown bubbles we use normal white-space flow.
  // Remove formatting newlines around block tags first so they don't become extra <br>.
  s = s.replace(/>\n+/g, ">");
  s = s.replace(/\n+</g, "<");
  // Keep paragraph breaks for readability, but avoid whitespace inflation.
  s = s.replace(/\n{3,}/g, "\n\n");
  s = s.replace(/\n\n/g, "<br><br>");
  s = s.replace(/\n/g, "<br>");

  s = s.replace(/@@INLINE_(\d+)@@/g, (_m, i) => inlineCodes[Number(i)] || "");
  s = s.replace(/@@FENCED_(\d+)@@/g, (_m, i) => fenced[Number(i)] || "");
  s = s.replace(/@@HTML_BLOCK_(\w+)@@/g, (_m, key) =>
    `<div class="html-inline-preview" data-htmlkey="${key}"></div>`);
  return s;
}
