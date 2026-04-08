/**
 * transsion/auth-widget.js — Community login status card for Settings Channels tab.
 *
 * Injected via registerSettingsWidget() so settings.js knows nothing about it.
 * Shows whether the community SSO token is active, with a hint to log in if not.
 */

import { registerSettingsWidget } from "../modules/settings.js";
import { isAuthed, getUser, clearToken } from "./auth.js";

registerSettingsWidget((container) => {
  const card = document.createElement("div");
  card.className = "conn-section forum-settings-widget";

  if (isAuthed()) {
    const user = getUser();
    const name = user?.displayName || user?.email || "unknown";
    card.innerHTML = `
      <div class="conn-section-header">
        <span class="conn-platform-icon">🏘</span>
        <div class="conn-platform-info">
          <span class="conn-platform-name">Community Forum</span>
          <span class="conn-platform-desc">
            已登录为 <strong>${_esc(name)}</strong> · ${_esc(user?.email || "")}
          </span>
        </div>
        <span class="conn-configured-badge" style="background:var(--ok,#5cb85c);color:#fff">已认证</span>
        <button class="secondary" id="forum-settings-logout" style="font-size:11px;padding:3px 8px">退出</button>
      </div>`;
    card.querySelector("#forum-settings-logout")?.addEventListener("click", () => {
      clearToken();
      document.dispatchEvent(new CustomEvent("hc:forum-unauthed"));
      _rerender(card);
    });
  } else {
    card.innerHTML = `
      <div class="conn-section-header">
        <span class="conn-platform-icon">🏘</span>
        <div class="conn-platform-info">
          <span class="conn-platform-name">Community Forum</span>
          <span class="conn-platform-desc">
            在 Model 页面选择 <strong>Transsion / TEX AI</strong> 并完成登录，Forum Tab 将自动出现。
          </span>
        </div>
        <span class="conn-configured-badge" style="color:var(--muted)">未登录</span>
      </div>`;
  }

  container.appendChild(card);
});

function _esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function _rerender(card) {
  // Replace the widget card with a fresh render
  const parent = card.parentElement;
  if (!parent) return;
  card.remove();
  // Lazy re-import to avoid circular re-execution issues
  import("./auth-widget.js").then(() => {}).catch(() => {});
}
