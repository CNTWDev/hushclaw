const BOX_DRAWING_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/;
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
  const boxChars = (trimmed.match(BOX_DRAWING_RE) || []).length;
  return boxChars >= 2 || /^[┌├└╭╞╰╔╠╚│║]/.test(trimmed);
}

function fenceBoxDrawingRuns(text) {
  const lines = normalizeCompactBoxRows(text).split("\n");
  const out = [];
  let run = [];

  const flush = () => {
    if (!run.length) return;
    const meaningful = run.filter((line) => line.trim());
    if (meaningful.length >= 2 && meaningful.every(isBoxDrawingLine)) {
      out.push("```text");
      out.push(...run);
      out.push("```");
    } else {
      out.push(...run);
    }
    run = [];
  };

  for (const line of lines) {
    if (isBoxDrawingLine(line)) {
      run.push(line);
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
  if (!BOX_DRAWING_RE.test(text)) return text;

  let cursor = 0;
  let out = "";
  for (const match of text.matchAll(FENCE_RE)) {
    out += fenceBoxDrawingRuns(text.slice(cursor, match.index));
    out += match[0];
    cursor = (match.index || 0) + match[0].length;
  }
  out += fenceBoxDrawingRuns(text.slice(cursor));
  return out;
}
