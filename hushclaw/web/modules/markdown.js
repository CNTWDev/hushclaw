/**
 * markdown.js — Safe markdown renderer for chat bubbles.
 * Keeps output HTML sanitized by escaping first, then controlled replacements.
 */

import { escHtml } from "./state.js";

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
  let s = escHtml(String(raw).replace(/\r\n?/g, "\n"));

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

  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, href) => {
    return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
  });

  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

  s = s.replace(/^######[ \t]+(.+)$/gm, "<h6>$1</h6>");
  s = s.replace(/^#####[ \t]+(.+)$/gm, "<h5>$1</h5>");
  s = s.replace(/^####[ \t]+(.+)$/gm, "<h4>$1</h4>");
  s = s.replace(/^###[ \t]+(.+)$/gm, "<h3>$1</h3>");
  s = s.replace(/^##[ \t]+(.+)$/gm, "<h2>$1</h2>");
  s = s.replace(/^#[ \t]+(.+)$/gm, "<h1>$1</h1>");
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

  s = s.replace(/\/files\/([\w.\-]+)/g, (_, fid) => {
    const apiKey = new URLSearchParams(location.search).get("api_key") || "";
    const href = apiKey ? `/files/${fid}?api_key=${encodeURIComponent(apiKey)}` : `/files/${fid}`;
    const name = fid.includes("_") ? fid.split("_").slice(1).join("_") : fid;
    return `<a class="dl-link" href="${href}" download="${escHtml(name)}">⬇ ${escHtml(name)}</a>`;
  });

  s = s.replace(/@@INLINE_(\d+)@@/g, (_m, i) => inlineCodes[Number(i)] || "");
  s = s.replace(/@@FENCED_(\d+)@@/g, (_m, i) => fenced[Number(i)] || "");
  // Avoid large visual gaps caused by excessive blank lines in source markdown.
  s = s.replace(/\n{3,}/g, "\n\n");
  return s;
}
