/**
 * events/upload.js — File upload, attachment chip rendering, paste image extraction.
 *
 * Extracted from events.js. No dependency on events.js — avoids circular imports.
 */

import { state, els, escHtml } from "../state.js";
import { insertSystemMsg } from "../chat.js";

// ── File upload ────────────────────────────────────────────────────────────

export async function uploadFile(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const b64      = reader.result.split(",")[1];
      const uploadId = Math.random().toString(36).slice(2);
      state._uploadPending.set(uploadId, resolve);
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          type: "file_upload",
          upload_id: uploadId,
          name: file.name,
          data: b64,
        }));
      } else {
        state._uploadPending.delete(uploadId);
        resolve({ ok: false, error: "Not connected" });
      }
    };
    reader.onerror = () => resolve({ ok: false, error: "FileReader error" });
    reader.readAsDataURL(file);
  });
}

// ── Attachment helpers ─────────────────────────────────────────────────────

const _IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp"]);

export function isImageFile(name) {
  const ext = (name || "").split(".").pop().toLowerCase();
  return _IMAGE_EXTS.has(ext);
}

function _extFromMime(type) {
  const t = String(type || "").toLowerCase();
  if (t.endsWith("/jpeg") || t.endsWith("/jpg")) return "jpg";
  if (t.endsWith("/png")) return "png";
  if (t.endsWith("/gif")) return "gif";
  if (t.endsWith("/webp")) return "webp";
  if (t.endsWith("/bmp")) return "bmp";
  return "png";
}

function _withApiKey(url) {
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  if (!apiKey || !url) return url;
  return url.includes("?")
    ? `${url}&api_key=${encodeURIComponent(apiKey)}`
    : `${url}?api_key=${encodeURIComponent(apiKey)}`;
}

function _normalizePastedImage(file, index = 0) {
  if (!file) return null;
  const hasName = !!(file.name && file.name.trim());
  if (hasName && isImageFile(file.name)) return file;
  const ext = _extFromMime(file.type);
  const ts = Date.now();
  const name = `pasted-image-${ts}-${index + 1}.${ext}`;
  try {
    return new File([file], name, {
      type: file.type || `image/${ext}`,
      lastModified: Date.now(),
    });
  } catch {
    return file;
  }
}

export function extractPastedImages(ev) {
  const dt = ev.clipboardData;
  if (!dt) return [];
  const out = [];
  const items = Array.from(dt.items || []);
  for (const item of items) {
    if (item.kind !== "file") continue;
    if (!String(item.type || "").toLowerCase().startsWith("image/")) continue;
    const f = item.getAsFile();
    if (f) out.push(f);
  }
  if (!out.length) {
    for (const f of Array.from(dt.files || [])) {
      if (String(f.type || "").toLowerCase().startsWith("image/")) out.push(f);
    }
  }
  return out.map((f, i) => _normalizePastedImage(f, i)).filter(Boolean);
}

export function renderAttachmentChips() {
  const chips = els.attachmentChips;
  if (!chips) return;
  chips.innerHTML = "";
  if (!state._attachments.length) {
    chips.classList.add("hidden");
    return;
  }
  chips.classList.remove("hidden");
  state._attachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.title = att.name;

    if (isImageFile(att.name) && att.preview_url) {
      const img = document.createElement("img");
      img.src = att.preview_url;
      img.className = "attach-chip-thumb";
      img.alt = att.name;
      chip.appendChild(img);
      const label = document.createElement("span");
      label.textContent = att.name;
      chip.appendChild(label);
    } else {
      chip.innerHTML = `<span>📄 ${escHtml(att.name)}</span>`;
    }

    const rm = document.createElement("button");
    rm.textContent = "✕";
    rm.title = "Remove";
    rm.addEventListener("click", () => {
      state._attachments.splice(idx, 1);
      renderAttachmentChips();
    });
    chip.appendChild(rm);
    chips.appendChild(chip);
  });
}

export function addExistingAttachment(file) {
  const previewUrl = isImageFile(file.name)
    ? _withApiKey(file.preview_url || file.url)
    : null;
  state._attachments.push({
    file_id: file.file_id,
    name: file.name,
    url: file.url,
    preview_url: previewUrl,
  });
  renderAttachmentChips();
}

export async function addFilesAsAttachments(files) {
  for (const file of files) {
    const previewUrl = isImageFile(file.name) ? URL.createObjectURL(file) : null;
    const result = await uploadFile(file);
    if (result.ok) {
      state._attachments.push({
        file_id: result.file_id,
        name: result.name,
        url: result.url,
        preview_url: previewUrl,
      });
      renderAttachmentChips();
    } else {
      insertSystemMsg(`Upload failed: ${result.error || "unknown error"}`);
    }
  }
}
