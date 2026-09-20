/** Browser downloads with a safe fallback for restricted/embedded browsers. */
export function markdownFilename({ markdown = '', sessionTitle = '', question = '' } = {}) {
  const clean = value => String(value || '').normalize('NFC')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]*>/g, '')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/[`*_~#]/g, '')
    .replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, ' ')
    .replace(/[<>:"/\\|?*：？]/g, ' ')
    .replace(/\s+/g, ' ').trim()
    .replace(/^\.+\s*/, '')
    .replace(/\.md$/i, '')
    .replace(/[.。！!，,；;\s…]+$/g, '');
  const usable = title => title.length > 1 &&
    !/^(?:Session\b.*|Chat|New (?:Topic|Session)|新对话|新建对话|对话|回答|回复|总结|结论|核心结论|建议|概述|说明|Question|Answer|Response|Summary|Overview)$/i.test(title);
  // Ignore headings inside code examples. A document's top-level title is more
  // specific than its session; arbitrary subsection headings are not titles.
  let fence = '';
  const prose = String(markdown).split('\n').filter(line => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = '';
      return false;
    }
    return !fence;
  }).join('\n');
  const heading = prose.match(/^ {0,3}#\s+(.+)$/m)?.[1] ||
    prose.match(/^([^\n]+)\n {0,3}={3,}\s*$/m)?.[1] || '';
  const prompt = String(question).split(/[\n。！？!?]/)[0]
    .replace(/^(?:请问|请|帮我|麻烦)(?:帮我)?(?:分析一下|分析下|总结一下|总结下|介绍一下|介绍下|看看|看一下)?[，,\s]*/, '');
  let title = [heading, sessionTitle, prompt].map(clean).find(usable) || '对话记录';
  const chars = Array.from(title);
  if (chars.length > 40) {
    title = chars.slice(0, 40).join('');
    // Avoid cutting an English word while retaining a useful title.
    if (/\s/.test(title) && /[a-z0-9]/i.test(chars[40])) title = title.replace(/\s+\S*$/, '') || title;
  }
  title = title.replace(/[.\s,，;；-]+$/g, '');
  if (/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(title)) title = `对话-${title}`;
  return `${title}.md`;
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  try {
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // Download consumption is asynchronous, particularly in Safari/WebViews.
    // Revoking in the click task can invalidate a download before it starts.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

export async function saveMarkdownFile(text, filename) {
  let writable;
  if (typeof window.showSaveFilePicker === "function") {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: "Markdown", accept: { "text/markdown": [".md"] } }],
      });
      writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      return "saved";
    } catch (error) {
      if (writable) {
        try { await writable.abort(); } catch (_) { /* Already closed/aborted. */ }
      }
      // Cancelling a save dialog is not a failure or permission to download.
      if (error?.name === "AbortError") return "cancelled";
      // Feature detection is not a permission/support check. An exposed picker
      // can reject in an embedded browser, iframe or restricted environment.
      console.warn("[download] Markdown save picker failed; using browser download", {
        name: error?.name || "Error",
      });
    }
  }
  downloadBlob(new Blob([text], { type: "text/markdown;charset=utf-8" }), filename);
  return "downloaded";
}
