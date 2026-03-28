/**
 * modal.js — lightweight shared modal helpers for consistent dialogs.
 */

let _overlay = null;
let _activeCleanup = null;

function _ensureOverlay() {
  if (_overlay) return _overlay;
  const root = document.createElement("div");
  root.id = "app-modal-overlay";
  root.className = "app-modal-overlay hidden";
  root.innerHTML = `
    <div class="app-modal-card" role="dialog" aria-modal="true" aria-live="polite">
      <div class="app-modal-header">
        <h3 class="app-modal-title" id="app-modal-title"></h3>
      </div>
      <div class="app-modal-body" id="app-modal-body"></div>
      <div class="app-modal-footer" id="app-modal-footer"></div>
    </div>
  `;
  document.body.appendChild(root);
  _overlay = root;
  return _overlay;
}

function _closeCurrent() {
  if (!_overlay) return;
  _overlay.classList.add("hidden");
  if (_activeCleanup) {
    try { _activeCleanup(); } catch (_) {}
    _activeCleanup = null;
  }
}

function _openModal({ title = "", body = "", bodyIsHtml = false, actions = [], closeOnBackdrop = true }) {
  const overlay = _ensureOverlay();
  const card = overlay.querySelector(".app-modal-card");
  const titleEl = overlay.querySelector("#app-modal-title");
  const bodyEl = overlay.querySelector("#app-modal-body");
  const footerEl = overlay.querySelector("#app-modal-footer");

  titleEl.textContent = title || "";
  if (bodyIsHtml) bodyEl.innerHTML = body;
  else bodyEl.textContent = body || "";
  footerEl.innerHTML = "";

  const onKeydown = (ev) => {
    if (ev.key === "Escape") _closeCurrent();
  };

  actions.forEach((act, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = act.secondary ? "secondary" : "";
    btn.textContent = act.label || `Action ${idx + 1}`;
    btn.addEventListener("click", () => {
      if (act.onClick) act.onClick();
    });
    footerEl.appendChild(btn);
  });

  const onOverlayClick = (ev) => {
    if (!closeOnBackdrop) return;
    if (!card.contains(ev.target)) _closeCurrent();
  };

  window.addEventListener("keydown", onKeydown);
  overlay.addEventListener("click", onOverlayClick);

  _activeCleanup = () => {
    window.removeEventListener("keydown", onKeydown);
    overlay.removeEventListener("click", onOverlayClick);
    footerEl.innerHTML = "";
  };

  overlay.classList.remove("hidden");
}

export function openConfirm({
  title = "Confirm",
  message = "",
  confirmText = "Confirm",
  cancelText = "Cancel",
  closeOnBackdrop = true,
}) {
  return new Promise((resolve) => {
    _openModal({
      title,
      body: message,
      bodyIsHtml: false,
      closeOnBackdrop,
      actions: [
        { label: cancelText, secondary: true, onClick: () => { _closeCurrent(); resolve(false); } },
        { label: confirmText, secondary: false, onClick: () => { _closeCurrent(); resolve(true); } },
      ],
    });
  });
}

export function openDialog({
  title = "",
  html = "",
  actions = [],
  closeOnBackdrop = true,
}) {
  _openModal({
    title,
    body: html,
    bodyIsHtml: true,
    closeOnBackdrop,
    actions: actions.map((a) => ({
      label: a.label,
      secondary: Boolean(a.secondary),
      onClick: () => {
        if (a.onClick) a.onClick();
      },
    })),
  });
  return _closeCurrent;
}

export function closeModal() {
  _closeCurrent();
}
