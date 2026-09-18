/** Browser downloads with a safe fallback for restricted/embedded browsers. */
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
