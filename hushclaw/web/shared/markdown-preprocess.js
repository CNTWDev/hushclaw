const BOX_DRAWING_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/;
const BOX_DRAWING_GLOBAL_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/g;
const ALIGNMENT_GAP_RE = /\S(?:.*\S)?(?: {2,}|\t+)\S/;
const MARKDOWN_STRUCTURAL_LINE_RE = /^\s{0,3}(?:#{1,6}\s|[-*+]\s|>|\d+\.\s|\|)/;
const FENCE_RE = /```[\s\S]*?```/g;
const CODE_REGION_RE = /```[\s\S]*?(?:```|$)|`[^`\n]*`/g;
const LOOSE_BLOCK_MATH_RE = /^([ \t]*)\[\s*([^\]\n]+?)\s*\][ \t]*$/gm;

function looksLikeMathExpression(value) {
  const text = String(value || "").trim();
  if (!/\d/.test(text)) return false;
  return /\\[A-Za-z]+/.test(text)
    || /[=×÷±≤≥≠≈∞∑∏√^_]/.test(text)
    || /\s[+*/-]\s/.test(text);
}

function normalizeMathUnicodeText(value) {
  return String(value || "")
    .split(/(\\(?:text|mathrm|operatorname)\{[^{}]*\})/g)
    .map((part, index) => (
      index % 2 === 1
        ? part
        : part.replace(/([\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+)/g, "\\text{$1}")
    ))
    .join("");
}

function normalizeMathBody(value) {
  let text = String(value || "")
    // A naked percent starts a TeX comment, but model output commonly uses it
    // as a literal percentage. Preserve explicit \% and repair the common case.
    .replace(/(^|[^\\])%/g, "$1\\%")
    // Keep thousands separators tight instead of treating commas as math
    // punctuation with surrounding space.
    .replace(/(\d),(?=\d{3}(?:\D|$))/g, "$1{,}")
    .trim();
  text = normalizeMathUnicodeText(text);
  // Model output often appends units directly to a value. Romanize those
  // units while keeping variables such as x/y untouched.
  return text.replace(/(\d)([A-Za-z]{2,})(?=(?:\\text\{|[/·*×]|$))/g, "$1\\,\\mathrm{$2}");
}

function normalizeMathTextSegment(value) {
  let text = String(value || "");

  // Streamdown's math plugin consumes dollar delimiters. Accept the LaTeX
  // delimiters models commonly emit and normalize them before parsing.
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_match, body) => (
    `$$\n${normalizeMathBody(body)}\n$$`
  ));
  text = text.replace(/\\\(([^\n]*?)\\\)/g, (_match, body) => (
    `$${normalizeMathBody(body)}$`
  ));

  // Tolerate a standalone bracketed equation when the model omitted the
  // backslashes around \[...\]. Ordinary bracketed prose is left untouched.
  text = text.replace(LOOSE_BLOCK_MATH_RE, (match, indent, body) => {
    if (!looksLikeMathExpression(body)) return match;
    return `${indent}$$\n${normalizeMathBody(body)}\n${indent}$$`;
  });

  // Repair percentages in already-canonical math too.
  text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_match, body) => (
    `$$\n${normalizeMathBody(body)}\n$$`
  ));
  text = text.replace(/(^|[^\\$])\$([^$\n]+?)\$(?!\$)/g, (match, prefix, body) => {
    if (!looksLikeMathExpression(body)) return match;
    return `${prefix}$${normalizeMathBody(body)}$`;
  });
  return text;
}

export function normalizeMathMarkdown(raw) {
  const text = String(raw ?? "");
  let cursor = 0;
  let out = "";
  for (const match of text.matchAll(CODE_REGION_RE)) {
    out += normalizeMathTextSegment(text.slice(cursor, match.index));
    out += match[0];
    cursor = (match.index || 0) + match[0].length;
  }
  out += normalizeMathTextSegment(text.slice(cursor));
  return out;
}

function normalizeCompactBoxRows(text) {
  return String(text || "")
    .replace(/([┐┤┘╮╯╗╣╝])(?=[│║├└┌╞╚╔╠╰╭])/g, "$1\n")
    .replace(/([│║])(?=[├└┌╞╚╔╠╰╭])/g, "$1\n")
    .replace(/([│║])(?=[│║])/g, "$1\n");
}

function isBoxDrawingLine(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed) return false;
  if (!BOX_DRAWING_RE.test(trimmed)) return false;
  const boxChars = (trimmed.match(BOX_DRAWING_GLOBAL_RE) || []).length;
  return boxChars >= 2 || /^[┌├└╭╞╰╔╠╚│║]/.test(trimmed);
}

function isAlignmentSensitiveLine(line) {
  const raw = String(line || "");
  const trimmed = raw.trim();
  if (!trimmed) return false;
  if (isBoxDrawingLine(raw)) return true;
  if (MARKDOWN_STRUCTURAL_LINE_RE.test(raw)) return false;
  return ALIGNMENT_GAP_RE.test(raw);
}

function shouldFenceAsPreformattedBlock(lines) {
  const meaningful = lines.filter((line) => String(line || "").trim());
  if (meaningful.length < 2) return false;
  if (meaningful.every(isBoxDrawingLine)) return true;
  const alignedLines = meaningful.filter(isAlignmentSensitiveLine);
  return alignedLines.length >= 2;
}

function fenceLayoutSensitiveRuns(text) {
  const lines = normalizeCompactBoxRows(text).split("\n");
  const out = [];
  let block = [];

  const flush = () => {
    if (!block.length) return;
    if (shouldFenceAsPreformattedBlock(block)) {
      out.push("```");
      out.push(...block);
      out.push("```");
    } else {
      out.push(...block);
    }
    block = [];
  };

  for (const line of lines) {
    if (!line.trim()) {
      flush();
      out.push(line);
      continue;
    }
    if (isAlignmentSensitiveLine(line) || block.length) {
      block.push(line);
      continue;
    }
    flush();
    out.push(line);
  }
  flush();
  return out.join("\n");
}

export function preprocessMarkdownForRendering(raw) {
  const text = normalizeMathMarkdown(raw);
  if (!BOX_DRAWING_RE.test(text) && !ALIGNMENT_GAP_RE.test(text)) return text;

  let cursor = 0;
  let out = "";
  for (const match of text.matchAll(FENCE_RE)) {
    out += fenceLayoutSensitiveRuns(text.slice(cursor, match.index));
    out += match[0];
    cursor = (match.index || 0) + match[0].length;
  }
  out += fenceLayoutSensitiveRuns(text.slice(cursor));
  return out;
}
