const BOX_DRAWING_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/;
const BOX_DRAWING_GLOBAL_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/g;
const ALIGNMENT_GAP_RE = /\S(?:.*\S)?(?: {2,}|\t+)\S/;
const MARKDOWN_STRUCTURAL_LINE_RE = /^\s{0,3}(?:#{1,6}\s|[-*+]\s|>|\d+\.\s|\|)/;
const FENCE_RE = /```[\s\S]*?```/g;

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
  const text = String(raw ?? "");
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
