/**
 * settings/transsion.js — Transsion auth flow, test-connection spinner,
 * Model tab renderer, and models-response handler.
 *
 * Owns all _tx* state so save.js can call getTxForSave() and
 * handlers.js can call setTxFromConfig() without circular deps.
 */

import { state, wizard, els, send, escHtml } from "../state.js";
import { PROVIDERS, providerById } from "./providers.js";
import { authApi as transsionAuthApi } from "../../transsion/api.js";
import { openDialog } from "../modal.js";

// ── Transsion auth state ────────────────────────────────────────────────────
let _txEmail           = "";
let _txDisplayName     = "";
let _txCodeRequested   = false;
let _txShowRelogin     = false;
// Kept only long enough to include in TOML save; forum plugin owns the live copy.
let _txAccessToken     = "";
// Models returned by the last successful Transsion login — persisted in
// localStorage so they survive modal close/re-open and page refreshes.
let _txCachedModels = (() => {
  try { return JSON.parse(localStorage.getItem("hc_tx_models") || "[]"); } catch { return []; }
})();

// ── Cross-module API ────────────────────────────────────────────────────────

/** Called by handlers.js from handleConfigStatus to sync saved credentials. */
export function setTxFromConfig(email, displayName, accessToken, authed) {
  _txEmail       = email       || "";
  _txDisplayName = displayName || "";
  _txAccessToken = accessToken || "";
  if (_txAccessToken && authed) {
    document.dispatchEvent(new CustomEvent("hc:transsion-authed", {
      detail: { accessToken: _txAccessToken, email: _txEmail, displayName: _txDisplayName },
    }));
  }
}

/** Called by save.js to include tx credentials in the config payload. */
export function getTxForSave() {
  return { email: _txEmail, displayName: _txDisplayName, accessToken: _txAccessToken };
}

// ── Test connection spinner ─────────────────────────────────────────────────

const _TEST_STEP_ICONS = {
  running: '<span class="test-step-spinner">⠋</span>',
  ok:      '<span class="test-step-icon ok">✓</span>',
  warn:    '<span class="test-step-icon warn">⚠</span>',
  error:   '<span class="test-step-icon error">✗</span>',
  skip:    '<span class="test-step-icon skip">–</span>',
};
const _TEST_SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
let _testSpinnerFrame = 0;
let _testSpinnerTimer = null;
let _testTimer        = null;

function _startSpinner(stepId) {
  _stopSpinner();
  _testSpinnerFrame = 0;
  _testSpinnerTimer = setInterval(() => {
    _testSpinnerFrame = (_testSpinnerFrame + 1) % _TEST_SPINNERS.length;
    const el = document.querySelector(`#wiz-test-steps [data-step="${stepId}"] .test-step-spinner`);
    if (el) el.textContent = _TEST_SPINNERS[_testSpinnerFrame];
  }, 80);
}

function _stopSpinner() {
  if (_testSpinnerTimer) { clearInterval(_testSpinnerTimer); _testSpinnerTimer = null; }
}

/** Called by handlers.js → resetWizardTimers to discard stale pending tests. */
export function clearTestTimer() {
  clearTimeout(_testTimer);
  _testTimer = null;
}

export function handleTestProviderStep(data) {
  const container = document.getElementById("wiz-test-steps");
  if (!container) return;

  const { step, status, label, detail } = data;
  let row = container.querySelector(`[data-step="${step}"]`);

  if (!row) {
    row = document.createElement("div");
    row.className = "test-step-row";
    row.dataset.step = step;
    container.appendChild(row);
  }

  if (status === "running") _startSpinner(step);
  else _stopSpinner();

  row.className = `test-step-row status-${status}`;
  row.innerHTML = `
    ${_TEST_STEP_ICONS[status] || ""}
    <span class="test-step-label">${escHtml(label)}</span>
    <span class="test-step-detail">${escHtml(detail)}</span>
  `;
}

export function handleTestProviderResult(data) {
  clearTimeout(_testTimer);
  _testTimer = null;
  _stopSpinner();
  const testBtn = document.getElementById("wiz-test-btn");
  if (testBtn) { testBtn.disabled = false; testBtn.textContent = "Test Connection"; }

  const container = document.getElementById("wiz-test-steps");
  if (!container) return;

  const summary = document.createElement("div");
  summary.className = `test-step-summary ${data.ok ? "ok" : "error"}`;
  summary.textContent = data.ok
    ? "✓ " + (data.detail || "All checks passed.")
    : "✗ " + (data.detail || "Connection failed.");
  container.appendChild(summary);
}

// ── Transsion auth flow handlers ────────────────────────────────────────────

function _txStatus(msg, kind = "info") {
  const el = document.getElementById("tx-status");
  if (!el) return;
  el.style.display = "block";
  el.className = `transsion-status transsion-status-${kind}`;
  el.textContent = msg;
}

/** Reset Transsion wizard buttons after a failed WS `error`. */
export function resetTranssionPendingUi(errorMessage = "") {
  const codeField = document.getElementById("tx-code-field");
  const sendBtn = document.getElementById("tx-send-code-btn");
  let touched = false;
  if (sendBtn && sendBtn.textContent === "Sending…") {
    touched = true;
    sendBtn.disabled = false;
    const showResend = codeField && codeField.style.display !== "none";
    sendBtn.textContent = showResend ? "Resend Code" : "Send Code";
  }
  const loginBtn = document.getElementById("tx-login-btn");
  if (loginBtn && loginBtn.textContent === "Logging in…") {
    touched = true;
    loginBtn.disabled = false;
    loginBtn.textContent = "Login & Authorize";
  }
  if (touched && errorMessage) {
    _txStatus(errorMessage, "error");
  }
}

export function handleTransssionCodeSent(data) {
  _txCodeRequested = true;
  const sendBtn = document.getElementById("tx-send-code-btn");
  if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "Resend Code"; }
  const codeField = document.getElementById("tx-code-field");
  if (codeField) codeField.style.display = "";
  const hint = document.getElementById("tx-send-hint");
  if (hint) hint.textContent = `Code sent to ${data.email || "your email"}.`;
  _txStatus("Verification code sent — check your inbox.", "info");
}

export function handleTransssionAuthed(data) {
  const loginBtn = document.getElementById("tx-login-btn");
  if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = "Login & Authorize"; }
  const name = data.display_name || data.email || "user";
  const quota = data.quota_remain ? ` · Quota: ${data.quota_remain}` : "";

  wizard.apiKey = (data.api_key || "").trim();
  const latestBaseUrl = (data.base_url || "").trim();
  if (latestBaseUrl) {
    wizard.baseUrl = latestBaseUrl;
  }
  _txEmail       = (data.email        || "").trim();
  _txDisplayName = (data.display_name || "").trim();
  _txAccessToken = (data.access_token || "").trim();
  _txCodeRequested = false;
  document.dispatchEvent(new CustomEvent("hc:transsion-authed", {
    detail: { accessToken: _txAccessToken, email: _txEmail, displayName: _txDisplayName },
  }));
  _txShowRelogin = false;

  const burlEl = document.getElementById("wiz-baseurl");
  if (burlEl && wizard.baseUrl) burlEl.value = wizard.baseUrl;

  if (Array.isArray(data.models) && data.models.length) {
    _txCachedModels = data.models;
    try { localStorage.setItem("hc_tx_models", JSON.stringify(data.models)); } catch { /* ignore */ }
  }

  if (Array.isArray(data.models) && data.models.length && !wizard.model) {
    wizard.model = data.models[0];
  }

  renderModelTab();

  if (Array.isArray(data.models) && data.models.length) {
    handleModelsResponse({ items: data.models });
  }

  const baseUrlHint = latestBaseUrl ? ` Endpoint synced: ${latestBaseUrl}` : " Endpoint not returned by auth response.";
  _txStatus(`✓ Signed in as ${name}${quota}.${baseUrlHint} Choose a model, then click Save.`, latestBaseUrl ? "ok" : "info");
}

export function handleTransssionQuotaResult(data) {
  const btn = document.getElementById("tx-quota-btn");
  if (btn) { btn.disabled = false; btn.textContent = "查看额度"; }

  if (!data.ok) {
    openDialog({
      title: "Token 额度",
      html: `<p style="color:var(--color-error,#e53)">${escHtml(data.error || "Unknown error")}</p>`,
    });
    return;
  }

  const info = data.info || {};

  const fmtQuota = (v) => {
    if (v == null || v === "") return "—";
    const n = parseFloat(v);
    if (isNaN(n)) return String(v);
    return "$" + n.toFixed(2);
  };
  const pct = (remain, total) => {
    const r = parseFloat(remain), t = parseFloat(total);
    if (!t || isNaN(r) || isNaN(t)) return null;
    return Math.max(0, Math.min(100, (r / t) * 100));
  };
  const fmtTs = (ts) => {
    if (!ts) return "—";
    try { return new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleString(); } catch { return String(ts); }
  };
  const statusLabel = info.status === 1
    ? `<span class="tx-quota-badge tx-quota-badge-active">ACTIVE</span>`
    : `<span class="tx-quota-badge tx-quota-badge-inactive">INACTIVE</span>`;
  const expiry = (info.unlimitedQuota || info.expiredTime == null)
    ? `<span class="tx-quota-badge tx-quota-badge-unlimited">永不过期</span>`
    : `<span class="tx-quota-expires">到期 ${fmtTs(info.expiredTime)}</span>`;

  const mkBar = (remainRaw, totalRaw, usedRaw) => {
    const p = pct(remainRaw, totalRaw);
    const bar = p != null
      ? `<div class="tx-quota-bar-track"><div class="tx-quota-bar-fill" style="width:${p.toFixed(1)}%"></div></div>`
      : "";
    const pLabel = p != null ? `剩余 ${p.toFixed(2)}%` : "";
    return `${bar}
      <div class="tx-quota-stat-row">
        <span class="tx-quota-stat">${fmtQuota(remainRaw)} <em>remaining</em></span>
        ${pLabel ? `<span class="tx-quota-pct">${pLabel}</span>` : ""}
        <span class="tx-quota-stat">${fmtQuota(usedRaw)} <em>used</em></span>
      </div>`;
  };

  const totalSection = (!info.unlimitedQuota && (info.remainQuota != null || info.usedQuota != null))
    ? `<div class="tx-quota-section">
        <div class="tx-quota-section-label">总额度</div>
        ${mkBar(info.remainQuota, (parseFloat(info.remainQuota||0) + parseFloat(info.usedQuota||0)) || null, info.usedQuota)}
      </div>` : "";

  const monthlySection = (info.monthlyQuota != null)
    ? `<div class="tx-quota-section">
        <div class="tx-quota-section-label">本月额度 <span class="tx-quota-monthly-total">${fmtQuota(info.monthlyQuota)} / 月</span></div>
        ${mkBar(info.monthlyRemaining, info.monthlyQuota, info.monthlyUsed)}
      </div>` : "";

  const footer = `
    <div class="tx-quota-footer">
      ${info.quotaRefreshedAt ? `<span>最后刷新：${fmtTs(info.quotaRefreshedAt)}</span>` : ""}
      ${info.tokenId != null ? `<span>Token ID: ${escHtml(String(info.tokenId))}</span>` : ""}
    </div>`;

  openDialog({
    title: "Token 额度",
    html: `
      <div class="tx-quota-dialog">
        <div class="tx-quota-header">
          <div class="tx-quota-identity">
            <span class="tx-quota-dot"></span>
            <strong class="tx-quota-name">${escHtml(info.name || "—")}</strong>
            ${statusLabel}
            ${expiry}
          </div>
        </div>
        ${totalSection}
        ${monthlySection}
        ${footer}
      </div>`,
    wideCard: true,
  });
}

// ── Model tab renderer ──────────────────────────────────────────────────────

export function renderModelTab() {
  const prov = providerById(wizard.provider);
  const sc   = wizard.serverConfig;

  let cardsHtml = `<div class="settings-section"><h3 class="settings-section-h">AI Provider</h3><div class="provider-cards" id="provider-cards">`;
  PROVIDERS.forEach((p) => {
    const sel = p.id === wizard.provider ? " selected" : "";
    cardsHtml += `
      <label class="provider-card${sel}" data-id="${p.id}">
        <input type="radio" name="provider" value="${p.id}" ${sel ? "checked" : ""}>
        <div class="provider-card-info">
          <div class="provider-card-name">${escHtml(p.name)}</div>
          <div class="provider-card-desc">${escHtml(p.desc)}</div>
        </div>
      </label>`;
  });
  cardsHtml += `</div></div>`;

  let keyHtml = `<div class="settings-section"><h3 class="settings-section-h">API Key &amp; Endpoint</h3>`;

  if (prov.authFlow === "email_code") {
    const ts = sc && sc.transsion;
    const savedAuthed = ts && ts.authed;
    const showRelogin = _txShowRelogin;
    const pendingSave =
      wizard.provider === "transsion" &&
      Boolean(wizard.apiKey && _txEmail) &&
      !savedAuthed;
    const displaySaved = savedAuthed ? escHtml(ts.display_name || ts.email) : "";

    let topBadge = "";
    if (savedAuthed && !showRelogin) {
      topBadge = `
        <div class="transsion-authed-badge">
          <span>&#10003; Saved &middot; signed in as <strong>${displaySaved}</strong> (${escHtml(ts.email)})</span>
          <div class="transsion-badge-actions">
            <button type="button" id="tx-quota-btn" class="transsion-quota-btn">查看额度</button>
            <button type="button" id="tx-relogin-btn" class="transsion-relogin-btn">Re-login</button>
          </div>
        </div>`;
    } else if (savedAuthed && showRelogin) {
      topBadge = `
        <div class="transsion-authed-badge transsion-authed-badge-dim">
          <span>Refreshing credentials for <strong>${displaySaved}</strong> (${escHtml(ts.email)})</span>
          <button type="button" id="tx-cancel-relogin-btn" class="transsion-relogin-btn">Cancel</button>
        </div>`;
    } else if (pendingSave) {
      topBadge = `<div class="transsion-pending-save">Signed in — pick a model below, then click <strong>Save</strong> at the bottom to store credentials.</div>`;
    }

    const showForm = !savedAuthed || showRelogin;
    const emailValue = escHtml((ts && ts.email) || _txEmail || "");
    const codeHidden = _txCodeRequested ? "" : "display:none";

    keyHtml += `
      ${topBadge}
      <div id="tx-login-form" style="${showForm ? "" : "display:none"}">
        <div class="wfield">
          <label>Transsion Enterprise Email</label>
          <div style="display:flex;gap:8px">
            <input type="email" id="tx-email" autocomplete="off"
                   placeholder="you@transsion.com" style="flex:1"
                   value="${emailValue}">
            <button type="button" id="tx-send-code-btn" class="secondary" style="white-space:nowrap">Send Code</button>
          </div>
          <div class="wfield-hint" id="tx-send-hint">Enter your @transsion.com email address, then click Send Code.</div>
        </div>
        <div class="wfield" id="tx-code-field" style="${codeHidden}">
          <label>Verification Code</label>
          <div style="display:flex;gap:8px">
            <input type="text" id="tx-code" autocomplete="off" inputmode="numeric"
                   placeholder="6-digit code" maxlength="6" style="flex:1">
            <button type="button" id="tx-login-btn" class="secondary" style="white-space:nowrap">Login &amp; Authorize</button>
          </div>
          <div class="wfield-hint">Check your inbox (expires in 5 min).</div>
        </div>
      </div>
      <div id="tx-status" class="transsion-status" style="display:none"></div>`;
  } else if (prov.needsKey) {
    const keyHint = (sc && sc.api_key_masked && sc.provider === prov.id)
      ? `<span class="conn-set-badge">set</span> ${escHtml(sc.api_key_masked)} — leave blank to keep.`
      : prov.keyHint;
    keyHtml += `
      <div class="wfield">
        <label>${escHtml(prov.keyLabel)}</label>
        <input type="password" id="wiz-apikey" placeholder="${escHtml(prov.keyPlaceholder)}"
               autocomplete="off" value="${escHtml(wizard.apiKey)}">
        <div class="wfield-hint">${keyHint}</div>
      </div>`;
  } else {
    keyHtml += `<p class="wdesc">${prov.keyHint}</p>`;
  }

  if (prov.baseUrlLabel) {
    const burl = wizard.baseUrl || prov.defaultBaseUrl;
    let regionBtns = "";
    if (prov.regions && prov.regions.length) {
      const chips = prov.regions.map((r) => {
        const active = (burl === r.url) ? ' style="font-weight:600;border-color:var(--accent)"' : "";
        return `<button type="button" class="secondary region-btn" data-url="${escHtml(r.url)}"${active}>${escHtml(r.label)}</button>`;
      }).join("");
      regionBtns = `<div style="display:flex;gap:6px;margin-bottom:8px">${chips}</div>`;
    }
    keyHtml += `
      <div class="wfield">
        <label>${escHtml(prov.baseUrlLabel)}</label>
        ${regionBtns}
        <input type="text" id="wiz-baseurl" placeholder="${escHtml(prov.defaultBaseUrl)}"
               value="${escHtml(wizard.baseUrl || prov.defaultBaseUrl)}">
        <div class="wfield-hint">Leave as-is unless you're using a proxy or custom endpoint.</div>
      </div>`;
  }

  if (prov.authFlow !== "email_code") {
    keyHtml += `
      <div style="margin-top:14px">
        <button type="button" id="wiz-test-btn" class="secondary">Test Connection</button>
        <div id="wiz-test-steps"></div>
      </div>`;
  }
  keyHtml += `</div>`;

  const suggestions  = prov.modelSuggestions;
  const currentModel = wizard.model || prov.defaultModel;
  const listId       = "wiz-model-list";
  const optionsHtml  = suggestions.map((m) => `<option value="${escHtml(m)}">`).join("");
  const refreshBtn = prov.authFlow === "email_code"
    ? `<button type="button" id="wiz-refresh-models-btn" class="secondary"
         style="font-size:11px;padding:2px 8px;margin-left:8px">↺ Refresh</button>`
    : "";
  const modelHtml = `
    <div class="settings-section">
      <h3 class="settings-section-h" style="display:flex;align-items:center;gap:4px">
        Model${refreshBtn}
      </h3>
      <div class="wfield">
        <span id="wiz-model-loading" class="muted" style="font-size:12px">Fetching available models…</span>
        <select id="wiz-model-select" style="display:none"></select>
        <input type="text" id="wiz-model" list="${listId}"
               placeholder="${escHtml(prov.defaultModel)}"
               value="${escHtml(currentModel)}">
        <datalist id="${listId}">${optionsHtml}</datalist>
        <div class="wfield-hint">Select from list or type any model ID.</div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">
        ${suggestions.map((m) => `<button type="button" class="secondary model-chip" data-model="${escHtml(m)}">${escHtml(m)}</button>`).join("")}
      </div>
    </div>`;

  const cheapSuggestions = prov.cheapModelSuggestions || [];
  const cheapListId      = "wiz-cheap-model-list";
  const cheapOptHtml     = cheapSuggestions.map((m) => `<option value="${escHtml(m)}">`).join("");
  const cheapModelHtml = `
    <div class="settings-section">
      <h3 class="settings-section-h">Background model <span style="font-weight:400;opacity:0.6">(optional)</span></h3>
      <div class="wfield">
        <input type="text" id="sys-cheap-model" list="${cheapListId}"
               placeholder="${escHtml(cheapSuggestions[0] || '')}"
               value="${escHtml(wizard.cheapModel || '')}" autocomplete="off">
        <datalist id="${cheapListId}">${cheapOptHtml}</datalist>
        <div class="wfield-hint">Lightweight model for background tasks: profile learning, fact extraction, reflection, context compaction. Leave empty to use the main model for all tasks.</div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">
        ${cheapSuggestions.map((m) => `<button type="button" class="secondary cheap-model-chip" data-model="${escHtml(m)}">${escHtml(m)}</button>`).join("")}
      </div>
    </div>`;

  els.wizardBody.innerHTML = cardsHtml + keyHtml + modelHtml + cheapModelHtml;

  els.wizardBody.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      wizard.provider = radio.value;
      const p2 = providerById(wizard.provider);
      wizard.model   = p2.defaultModel;
      wizard.baseUrl = p2.defaultBaseUrl || "";
      if (p2.id !== "transsion") _txCodeRequested = false;
      renderModelTab();
    });
  });
  els.wizardBody.querySelectorAll(".provider-card").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector("input[type=radio]");
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
    });
  });

  const keyEl  = document.getElementById("wiz-apikey");
  const burlEl = document.getElementById("wiz-baseurl");
  if (keyEl)  keyEl.addEventListener("input",  () => { wizard.apiKey  = keyEl.value.trim(); });
  if (burlEl) burlEl.addEventListener("input", () => { wizard.baseUrl = burlEl.value.trim(); });

  els.wizardBody.querySelectorAll(".region-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const url = btn.dataset.url;
      wizard.baseUrl = url;
      if (burlEl) burlEl.value = url;
      els.wizardBody.querySelectorAll(".region-btn").forEach((b) => {
        b.style.fontWeight = "";
        b.style.borderColor = "";
      });
      btn.style.fontWeight = "600";
      btn.style.borderColor = "var(--accent)";
    });
  });

  // ── Transsion email-code login handlers ────────────────────────────────────

  const txReloginBtn = document.getElementById("tx-relogin-btn");
  const txCancelBtn  = document.getElementById("tx-cancel-relogin-btn");
  const txQuotaBtn   = document.getElementById("tx-quota-btn");
  if (txReloginBtn) {
    txReloginBtn.addEventListener("click", () => {
      _txShowRelogin   = true;
      _txCodeRequested = false;
      renderModelTab();
    });
  }
  if (txCancelBtn) {
    txCancelBtn.addEventListener("click", () => {
      _txShowRelogin   = false;
      _txCodeRequested = false;
      renderModelTab();
    });
  }
  if (txQuotaBtn) {
    txQuotaBtn.addEventListener("click", () => {
      txQuotaBtn.disabled = true;
      txQuotaBtn.textContent = "Loading…";
      send({ type: "transsion_quota" });
    });
  }

  const txSendBtn  = document.getElementById("tx-send-code-btn");
  const txLoginBtn = document.getElementById("tx-login-btn");
  if (txSendBtn) {
    txSendBtn.addEventListener("click", async () => {
      const email = (document.getElementById("tx-email")?.value || "").trim();
      if (!email) { _txStatus("Enter your email address first.", "error"); return; }
      txSendBtn.disabled = true;
      txSendBtn.textContent = "Sending…";
      const hint = document.getElementById("tx-send-hint");
      if (hint) hint.textContent = "Sending verification code…";
      try {
        await transsionAuthApi.sendEmailCode(email);
        handleTransssionCodeSent({ email });
      } catch (err) {
        txSendBtn.disabled = false;
        txSendBtn.textContent = "Send Code";
        _txStatus(`Failed to send code: ${err.message}`, "error");
      }
    });
  }
  if (txLoginBtn) {
    txLoginBtn.addEventListener("click", async () => {
      const email = (document.getElementById("tx-email")?.value || "").trim();
      const code  = (document.getElementById("tx-code")?.value  || "").trim();
      if (!email || !code) { _txStatus("Enter both email and verification code.", "error"); return; }
      txLoginBtn.disabled = true;
      txLoginBtn.textContent = "Logging in…";
      _txStatus("Authenticating…", "info");
      try {
        const data = await transsionAuthApi.login(email, code);
        handleTransssionAuthed(data);
      } catch (err) {
        txLoginBtn.disabled = false;
        txLoginBtn.textContent = "Login & Authorize";
        _txStatus(`Login failed: ${err.message}`, "error");
      }
    });
  }

  const testBtn = document.getElementById("wiz-test-btn");
  if (testBtn) {
    testBtn.addEventListener("click", () => {
      clearTimeout(_testTimer);
      _stopSpinner();
      testBtn.disabled = true;
      testBtn.textContent = "Testing…";
      const stepsEl = document.getElementById("wiz-test-steps");
      if (stepsEl) stepsEl.innerHTML = "";
      _testTimer = setTimeout(() => {
        _testTimer = null;
        _stopSpinner();
        const btn = document.getElementById("wiz-test-btn");
        if (btn) { btn.disabled = false; btn.textContent = "Test Connection"; }
        const c = document.getElementById("wiz-test-steps");
        if (c) {
          const s = document.createElement("div");
          s.className = "test-step-summary error";
          s.textContent = "✗ Timed out (30 s). Check your API key and endpoint.";
          c.appendChild(s);
        }
      }, 30000);
      send({ type: "test_provider", provider: wizard.provider, api_key: wizard.apiKey, base_url: wizard.baseUrl, model: wizard.model });
    });
  }

  const modelEl  = document.getElementById("wiz-model");
  const selectEl = document.getElementById("wiz-model-select");
  if (modelEl)  modelEl.addEventListener("input",  () => { wizard.model = modelEl.value.trim(); });
  if (selectEl) selectEl.addEventListener("change", () => {
    wizard.model = selectEl.value;
    if (modelEl) modelEl.value = selectEl.value;
  });
  els.wizardBody.querySelectorAll(".model-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      wizard.model = chip.dataset.model;
      if (modelEl) modelEl.value = wizard.model;
      if (selectEl && selectEl.style.display !== "none") selectEl.value = wizard.model;
    });
  });

  const cheapModelEl = document.getElementById("sys-cheap-model");
  if (cheapModelEl) cheapModelEl.addEventListener("input", () => { wizard.cheapModel = cheapModelEl.value.trim(); });
  els.wizardBody.querySelectorAll(".cheap-model-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      wizard.cheapModel = chip.dataset.model;
      if (cheapModelEl) cheapModelEl.value = wizard.cheapModel;
    });
  });

  const refreshModelsBtn = document.getElementById("wiz-refresh-models-btn");
  if (refreshModelsBtn) {
    refreshModelsBtn.addEventListener("click", () => {
      refreshModelsBtn.disabled = true;
      refreshModelsBtn.textContent = "↺ Refreshing…";
      const loadEl = document.getElementById("wiz-model-loading");
      const selEl  = document.getElementById("wiz-model-select");
      const inpEl  = document.getElementById("wiz-model");
      if (loadEl) loadEl.style.display = "";
      if (selEl)  { selEl.style.display = "none"; }
      if (inpEl)  { inpEl.style.display = ""; }
      const token = _txAccessToken || (wizard.serverConfig && wizard.serverConfig.transsion && wizard.serverConfig.transsion.access_token) || "";
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          type: "list_models", provider: wizard.provider,
          api_key: wizard.apiKey, base_url: wizard.baseUrl || prov.defaultBaseUrl,
          access_token: token,
        }));
      }
      setTimeout(() => {
        const b = document.getElementById("wiz-refresh-models-btn");
        if (b) { b.disabled = false; b.textContent = "↺ Refresh"; }
      }, 4000);
    });
  }

  const savedTranssionReady =
    sc &&
    sc.provider === "transsion" &&
    sc.api_key_set &&
    sc.transsion &&
    sc.transsion.authed;
  const skipListModels =
    prov.authFlow === "email_code" &&
    !wizard.apiKey &&
    !savedTranssionReady;

  const loadingEl = document.getElementById("wiz-model-loading");
  if (state.ws && state.ws.readyState === WebSocket.OPEN && !skipListModels) {
    const _txToken = _txAccessToken || (sc && sc.transsion && sc.transsion.access_token) || "";
    state.ws.send(JSON.stringify({
      type: "list_models", provider: wizard.provider,
      api_key: wizard.apiKey, base_url: wizard.baseUrl || prov.defaultBaseUrl,
      access_token: _txToken,
    }));
    if (wizard.provider === "transsion" && _txCachedModels.length) {
      handleModelsResponse({ items: _txCachedModels });
    }
  } else {
    loadingEl?.remove();
    if (wizard.provider === "transsion" && _txCachedModels.length) {
      handleModelsResponse({ items: _txCachedModels });
    }
  }
}

export function handleModelsResponse(msg) {
  if (!wizard.open || wizard.tab !== "model") return;
  const loadingEl = document.getElementById("wiz-model-loading");
  const selectEl  = document.getElementById("wiz-model-select");
  const inputEl   = document.getElementById("wiz-model");

  if (loadingEl) loadingEl.remove();

  const items = (msg.items && msg.items.length > 0)
    ? msg.items
    : (wizard.provider === "transsion" && _txCachedModels.length ? _txCachedModels : []);

  if (items.length > 0) {
    msg = { ...msg, items };
    const currentVal = wizard.model || providerById(wizard.provider).defaultModel;
    let opts = "";
    if (!msg.items.includes(currentVal)) {
      opts += `<option value="${escHtml(currentVal)}" selected>${escHtml(currentVal)}</option>`;
    }
    opts += msg.items.map((id) =>
      `<option value="${escHtml(id)}"${id === currentVal ? " selected" : ""}>${escHtml(id)}</option>`
    ).join("");
    if (selectEl) {
      selectEl.innerHTML = opts;
      selectEl.style.display = "";
      if (inputEl) inputEl.style.display = "none";
    }
  }
}
