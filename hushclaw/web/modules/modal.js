/**
 * modal.js — lightweight shared modal helpers for consistent dialogs.
 *
 * Theming: styles use CSS variables from styles/theme-modes.css (data-theme / data-mode).
 */

let _overlay = null;
let _activeCleanup = null;
/** Optional: run when modal closes via Escape, backdrop, or header ✕ (e.g. openConfirm → false). */
let _backdropDismissHandler = null;

function _ensureOverlay() {
  if (_overlay) return _overlay;
  const root = document.createElement("div");
  root.id = "app-modal-overlay";
  root.className = "app-modal-overlay hidden";
  root.innerHTML = `
    <div class="app-modal-card" role="dialog" aria-modal="true" aria-labelledby="app-modal-title" aria-live="polite">
      <div class="app-modal-accent" aria-hidden="true"></div>
      <div class="app-modal-header">
        <div class="app-modal-brand">
          <span class="app-modal-brand-mark" aria-hidden="true">
            <svg viewBox="0 0 48 48" fill="none" role="img">
              <defs>
                <linearGradient id="modalLogoShell" x1="9" y1="8" x2="38" y2="39" gradientUnits="userSpaceOnUse">
                  <stop offset="0" stop-color="#6ad6ff"/>
                  <stop offset="0.46" stop-color="#7b85f8"/>
                  <stop offset="1" stop-color="#ff8a5b"/>
                </linearGradient>
                <linearGradient id="modalLogoGlow" x1="12" y1="12" x2="36" y2="36" gradientUnits="userSpaceOnUse">
                  <stop offset="0" stop-color="#c8fbff" stop-opacity="0.95"/>
                  <stop offset="1" stop-color="#ffffff" stop-opacity="0.1"/>
                </linearGradient>
              </defs>
              <rect x="6" y="6" width="36" height="36" rx="12" fill="url(#modalLogoShell)"/>
              <path d="M16 16.5h8.2c4.7 0 7.3 2.34 7.3 5.9 0 3.04-1.94 4.92-4.55 5.45l5.15 7.65h-6.38l-4.35-6.94H20.8v6.94H16V16.5Z" fill="#09111F"/>
              <path d="M20.8 24.35h2.66c2.22 0 3.34-.68 3.34-2.02 0-1.28-1.08-1.92-3.25-1.92H20.8v3.94Z" fill="url(#modalLogoGlow)"/>
              <circle cx="33.5" cy="14.5" r="3.5" fill="#ffe27a"/>
            </svg>
          </span>
          <div class="app-modal-title-wrap">
            <div class="app-modal-kicker">HushClaw</div>
            <h3 class="app-modal-title" id="app-modal-title"></h3>
          </div>
        </div>
        <button type="button" class="app-modal-close icon-btn" id="app-modal-close" aria-label="Close">✕</button>
      </div>
      <div class="app-modal-body" id="app-modal-body"></div>
      <div class="app-modal-footer" id="app-modal-footer"></div>
    </div>
  `;
  document.body.appendChild(root);
  _overlay = root;
  return _overlay;
}

/**
 * @param {{ invokeDismiss?: boolean }} [options] — if invokeDismiss === false, do not run backdrop-dismiss handler (action buttons).
 */
function _closeCurrent(options = {}) {
  if (!_overlay) return;
  const invokeDismiss = options.invokeDismiss !== false;
  const handler = _backdropDismissHandler;
  _backdropDismissHandler = null;
  if (invokeDismiss && handler) {
    try {
      handler();
    } catch (_) {
      /* ignore */
    }
  }
  _overlay.classList.add("hidden");
  if (_activeCleanup) {
    try {
      _activeCleanup();
    } catch (_) {
      /* ignore */
    }
    _activeCleanup = null;
  }
}

function _openModal({
  title = "",
  body = "",
  bodyIsHtml = false,
  actions = [],
  closeOnBackdrop = true,
  onBackdropDismiss = null,
  wideCard = false,
  blockEsc = false,
}) {
  const overlay = _ensureOverlay();
  // Remove prior listeners / footer so stacked openDialog/openConfirm calls do not leak handlers.
  if (_activeCleanup) {
    try {
      _activeCleanup();
    } catch (_) {
      /* ignore */
    }
    _activeCleanup = null;
  }
  _backdropDismissHandler = typeof onBackdropDismiss === "function" ? onBackdropDismiss : null;

  const card = overlay.querySelector(".app-modal-card");
  card.classList.toggle("app-modal-card--wide", Boolean(wideCard));
  const titleEl = overlay.querySelector("#app-modal-title");
  const bodyEl = overlay.querySelector("#app-modal-body");
  const footerEl = overlay.querySelector("#app-modal-footer");
  const closeBtn = overlay.querySelector("#app-modal-close");

  titleEl.textContent = title || "";
  if (bodyIsHtml) bodyEl.innerHTML = body;
  else bodyEl.textContent = body || "";
  footerEl.innerHTML = "";

  const onKeydown = (ev) => {
    if (ev.key === "Escape") {
      if (blockEsc) { ev.preventDefault(); return; }
      _closeCurrent();
    }
  };

  actions.forEach((act, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const parts = ["app-modal-btn"];
    parts.push(act.secondary ? "app-modal-btn--secondary" : "app-modal-btn--primary");
    if (act.danger) parts.push("app-modal-btn--danger");
    btn.className = parts.join(" ");
    btn.textContent = act.label || `Action ${idx + 1}`;
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (act.onClick) act.onClick();
    });
    footerEl.appendChild(btn);
  });

  const onOverlayClick = (ev) => {
    if (!closeOnBackdrop) return;
    if (!card.contains(ev.target)) _closeCurrent();
  };

  const onCloseClick = () => {
    _closeCurrent();
  };

  window.addEventListener("keydown", onKeydown);
  overlay.addEventListener("click", onOverlayClick);
  closeBtn.addEventListener("click", onCloseClick);

  _activeCleanup = () => {
    window.removeEventListener("keydown", onKeydown);
    overlay.removeEventListener("click", onOverlayClick);
    closeBtn.removeEventListener("click", onCloseClick);
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
  /** When true, primary button uses destructive styling (e.g. delete flows). */
  dangerConfirm = false,
}) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    _openModal({
      title,
      body: message,
      bodyIsHtml: false,
      closeOnBackdrop,
      wideCard: false,
      onBackdropDismiss: () => settle(false),
      actions: [
        {
          label: cancelText,
          secondary: true,
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            settle(false);
          },
        },
        {
          label: confirmText,
          secondary: false,
          danger: dangerConfirm,
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            settle(true);
          },
        },
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
    wideCard: true,
    actions: actions.map((a) => ({
      label: a.label,
      secondary: Boolean(a.secondary),
      danger: Boolean(a.danger),
      onClick: () => {
        if (a.onClick) a.onClick();
      },
    })),
  });
  return _closeCurrent;
}

export function closeModal() {
  _closeCurrent({ invokeDismiss: false });
}

/**
 * Open a non-dismissible modal for long-running operations (e.g. server upgrade).
 * The close button and backdrop are disabled while the operation runs.
 *
 * Returns a handle:
 *   handle.update(html)               — replace the body with new HTML
 *   handle.settle({ html, actions })  — finalize: re-enable close, show result + buttons
 */
export function openLiveModal({ title = "", html = "" } = {}) {
  _openModal({
    title,
    body: html,
    bodyIsHtml: true,
    closeOnBackdrop: false,
    blockEsc: true,
    wideCard: false,
    actions: [],
  });

  // Hide the ✕ button while the operation is in progress.
  const closeBtn = document.getElementById("app-modal-close");
  if (closeBtn) closeBtn.style.visibility = "hidden";

  return {
    update(newHtml) {
      const bodyEl = document.getElementById("app-modal-body");
      if (bodyEl) bodyEl.innerHTML = newHtml;
    },
    settle({ html: finalHtml = "", actions: finalActions = [] } = {}) {
      // Re-show ✕ and re-enable backdrop / ESC by swapping to a regular dialog.
      _openModal({
        title,
        body: finalHtml,
        bodyIsHtml: true,
        closeOnBackdrop: true,
        blockEsc: false,
        wideCard: false,
        actions: finalActions.map((a) => ({
          label: a.label,
          secondary: Boolean(a.secondary),
          danger: Boolean(a.danger),
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            if (a.onClick) a.onClick();
          },
        })),
      });
    },
  };
}
