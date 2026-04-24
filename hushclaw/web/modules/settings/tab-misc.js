/**
 * settings/tab-misc.js — openWizard, closeWizard, tab navigation renderer,
 * Channels tab, Memory tab, Integrations tab.
 * Also owns the settings-widget registry (for plugin injection).
 */

import { wizard, connectors, emailCfg, calendarCfg, els, send, escHtml } from "../state.js";
import { CHANNELS, _isConfigured } from "./providers.js";
import { renderModelTab } from "./transsion.js";
import { renderSystemTab } from "./tab-system.js";
import { syncFormToState, saveSettings } from "./save.js";

// ── Settings widget registry (plugin injection into Channels tab) ───────────
const _settingsWidgets = [];
/** Register a function that receives the Channels-tab container and appends its own widget. */
export function registerSettingsWidget(fn) { _settingsWidgets.push(fn); }

// ── Auto-save helpers (used by Channels tab) ────────────────────────────────
let _chanSaveTimer = null;
function _scheduleSave() {
  if (_chanSaveTimer) clearTimeout(_chanSaveTimer);
  _chanSaveTimer = setTimeout(() => { _chanSaveTimer = null; syncFormToState(); saveSettings(); }, 800);
}
function _saveNow() {
  if (_chanSaveTimer) { clearTimeout(_chanSaveTimer); _chanSaveTimer = null; }
  syncFormToState();
  saveSettings();
}

// ── Wizard open/close ───────────────────────────────────────────────────────

export function openWizard(dismissible = true) {
  wizard.open        = true;
  wizard.dismissible = dismissible;
  els.wizardOverlay.classList.remove("hidden");
  els.wbtnClose.style.display = (dismissible || wizard.savedOnce) ? "" : "none";
  renderSettingsModal();
}

export function closeWizard() {
  wizard.open = false;
  els.wizardOverlay.classList.add("hidden");
}

// ── Settings tab navigation ─────────────────────────────────────────────────

export function renderSettingsTabs() {
  const tabs = [
    { id: "model",        label: "Model" },
    { id: "system",       label: "System" },
    { id: "memory",       label: "Memory" },
    { id: "channels",     label: "Channels" },
    { id: "integrations", label: "Integrations" },
  ];
  els.settingsTabs.innerHTML = tabs.map((t) =>
    `<button class="settings-tab-btn${wizard.tab === t.id ? " active" : ""}" data-tab="${t.id}">${t.label}</button>`
  ).join("");
  els.settingsTabs.querySelectorAll(".settings-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      syncFormToState();
      wizard.tab = btn.dataset.tab;
      renderSettingsModal();
    });
  });
}

export function renderSettingsModal() {
  renderSettingsTabs();
  switch (wizard.tab) {
    case "model":        renderModelTab();        break;
    case "system":       renderSystemTab();       break;
    case "memory":       renderMemoryTab();       break;
    case "channels":     renderChannelsTab();     break;
    case "integrations": renderIntegrationsTab(); break;
    default:             renderModelTab();        break;
  }
}

// ── Channels tab ────────────────────────────────────────────────────────────

export function renderChannelsTab() {
  const status = wizard.connectorStatus || {};

  els.wizardBody.innerHTML =
    `<div class="conn-panel">` +
    CHANNELS.map((ch) => {
      const c          = connectors[ch.id];
      const on         = c.enabled;
      const configured = _isConfigured(ch.id, c);
      const isOnline   = status[ch.id] === true;
      const dotClass   = `conn-status-dot ${isOnline ? "online" : "offline"}`;
      const dotTitle   = isOnline ? "Connected" : (on ? "Starting / offline" : "Disabled");
      const badge      = (!on && configured)
        ? `<span class="conn-configured-badge" title="Previously configured — toggle to re-enable">configured</span>`
        : "";
      const isOpen = on;
      return `
        <div class="conn-section${isOpen ? " chan-open" : ""}" id="chan-${ch.id}">
          <div class="conn-section-header chan-header" data-target="${ch.id}-fields" style="cursor:pointer">
            <span class="chan-chevron">${isOpen ? "▾" : "▸"}</span>
            <span class="conn-platform-icon">${ch.icon}</span>
            <div class="conn-platform-info">
              <span class="conn-platform-name">${ch.name}</span>
              <span class="conn-platform-desc">${ch.desc}</span>
            </div>
            ${badge}
            <span class="${dotClass}" title="${dotTitle}"></span>
            <label class="toggle-switch" title="${on ? "Enabled" : "Disabled"}" onclick="event.stopPropagation()">
              <input type="checkbox" id="${ch.id}-enabled" ${on ? "checked" : ""}
                     data-chan="${ch.id}">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="conn-fields" id="${ch.id}-fields"${isOpen ? "" : ' style="display:none"'}>
            ${ch.fields(c)}
            <div class="wfield-hint" style="margin-top:4px">
              Setup guide: <a href="${ch.setupUrl}" target="_blank" rel="noopener">${ch.setupLabel} ↗</a>
            </div>
          </div>
        </div>`;
    }).join("") +
    `</div>`;

  CHANNELS.forEach(({ id }) => {
    const sectionEl = document.getElementById(`chan-${id}`);
    const headerEl  = sectionEl?.querySelector(".chan-header");
    const togEl     = document.getElementById(`${id}-enabled`);
    const fieldsEl  = document.getElementById(`${id}-fields`);
    if (!togEl || !fieldsEl || !headerEl) return;

    // Accordion header click
    headerEl.addEventListener("click", () => {
      const isOpen = fieldsEl.style.display !== "none";
      fieldsEl.style.display = isOpen ? "none" : "";
      const chevron = headerEl.querySelector(".chan-chevron");
      if (chevron) chevron.textContent = isOpen ? "▸" : "▾";
    });

    // Enable toggle: auto-expand + immediate save
    togEl.addEventListener("change", (e) => {
      if (e.target.checked) {
        fieldsEl.style.display = "";
        const chevron = headerEl.querySelector(".chan-chevron");
        if (chevron) chevron.textContent = "▾";
      }
      _saveNow();
    });

    // Field inputs: debounced save
    fieldsEl.querySelectorAll("input, select, textarea").forEach((el) => {
      el.addEventListener("change", _scheduleSave);
      if (el.type !== "checkbox") {
        el.addEventListener("input", _scheduleSave);
      } else {
        el.addEventListener("change", _saveNow);
      }
    });
  });

  const connPanel = els.wizardBody.querySelector(".conn-panel");
  if (connPanel) _settingsWidgets.forEach((fn) => { try { fn(connPanel); } catch { /* ignore */ } });
}

export function updateChannelStatusDots() {
  const status = wizard.connectorStatus || {};
  CHANNELS.forEach(({ id }) => {
    const dotEl = els.wizardBody?.querySelector(`#chan-${id} .conn-status-dot`);
    if (!dotEl) return;
    const isOnline = status[id] === true;
    const c = connectors[id];
    dotEl.className = `conn-status-dot ${isOnline ? "online" : "offline"}`;
    dotEl.title = isOnline ? "Connected" : (c?.enabled ? "Starting / offline" : "Disabled");
  });
}

// ── Memory tab ──────────────────────────────────────────────────────────────

export function renderMemoryTab() {
  const ws = wizard.workspaceStatus || {};
  const wsConfigured = ws.configured;
  const wsPath = ws.path || wizard.workspaceDir || "";
  const soulOk = ws.soul_md;
  const userOk = ws.user_md;

  const wsStatusBadge = wsConfigured
    ? `<span style="color:var(--green,#4caf50);font-weight:600">✓ Active</span>`
    : `<span style="color:var(--yellow,#ff9800);font-weight:600">⚠ Not initialized</span>`;
  const soulBadge = soulOk ? `<span style="color:var(--green,#4caf50)">✓ SOUL.md</span>` : `<span style="color:var(--muted,#888)">✗ SOUL.md missing</span>`;
  const userBadge = userOk ? `<span style="color:var(--green,#4caf50)">✓ USER.md</span>` : `<span style="color:var(--muted,#888)">✗ USER.md missing</span>`;

  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Workspace &amp; Memory Files</h3>
      <p class="wdesc">
        The workspace directory holds <code>SOUL.md</code> (agent identity, injected into every session)
        and <code>USER.md</code> (user notes, auto-updated after each turn).
        Setting this up is the fastest way to prevent HushClaw from "starting from scratch".
      </p>
      <div class="wfield">
        <label>Status: ${wsStatusBadge} &nbsp; ${soulBadge} &nbsp; ${userBadge}</label>
        <div class="wfield-hint" style="margin-top:4px">
          Active path: <code>${escHtml(wsPath || "(default: ~/.hushclaw/workspace or .hushclaw/ in cwd)")}</code>
        </div>
      </div>
      <div class="wfield">
        <label>Workspace Directory <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="mem-workspace-dir"
               placeholder="Leave blank to use default (~/.hushclaw/workspace)"
               value="${escHtml(wizard.workspaceDir || '')}">
        <div class="wfield-hint">
          Override the workspace path. Leave blank to use the global default.<br>
          HushClaw auto-detects <code>.hushclaw/</code> in the current directory first.
        </div>
      </div>
      ${!wsConfigured || !soulOk || !userOk ? `
      <div class="wfield">
        <button id="mem-init-workspace-btn" class="btn-secondary" style="margin-top:4px">
          🗂 Initialize Workspace (create SOUL.md &amp; USER.md)
        </button>
        <div id="mem-init-ws-status" class="wfield-hint" style="margin-top:4px"></div>
      </div>` : `
      <div class="wfield">
        <button id="mem-init-workspace-btn" class="btn-secondary" style="margin-top:4px">
          🔄 Re-seed missing files
        </button>
        <div id="mem-init-ws-status" class="wfield-hint" style="margin-top:4px"></div>
      </div>`}
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Context &amp; Compaction</h3>
      <p class="wdesc">Controls how much conversation history is kept in context and when old turns are archived.</p>
      <div class="wfield">
        <label>History budget (tokens)</label>
        <input type="number" id="mem-history-budget" min="0" max="200000" step="1000"
               value="${escHtml(String(wizard.historyBudget))}">
        <div class="wfield-hint">Maximum tokens of conversation history kept in context before compaction triggers. Set 0 to disable compaction by budget.</div>
      </div>
      <div class="wfield">
        <label>Compact threshold</label>
        <input type="number" id="mem-compact-threshold" min="0.1" max="1.0" step="0.05"
               value="${escHtml(String(wizard.compactThreshold))}">
        <div class="wfield-hint">Compact when history exceeds this fraction of the history budget (e.g. 0.85 = 85%).</div>
      </div>
      <div class="wfield">
        <label>Keep recent turns</label>
        <input type="number" id="mem-compact-keep-turns" min="1" max="50" step="1"
               value="${escHtml(String(wizard.compactKeepTurns))}">
        <div class="wfield-hint">Always preserve this many most-recent turns even after compaction.</div>
      </div>
      <div class="wfield">
        <label>Compact strategy</label>
        <select id="mem-compact-strategy">
          <option value="lossless"  ${wizard.compactStrategy === "lossless"  ? "selected" : ""}>lossless — archive to memory store, replace with summary bullets</option>
          <option value="summarize" ${wizard.compactStrategy === "summarize" ? "selected" : ""}>summarize — LLM-generated summary (uses extra tokens)</option>
        </select>
        <div class="wfield-hint">How old turns are handled when the history budget is exceeded.</div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Memory Retrieval</h3>
      <p class="wdesc">Controls how memories are scored, retrieved, and injected into each request.</p>
      <div class="wfield">
        <label>Min relevance score</label>
        <input type="number" id="mem-min-score" min="0" max="1.0" step="0.05"
               value="${escHtml(String(wizard.memoryMinScore))}">
        <div class="wfield-hint">Memories scoring below this threshold are not injected (0.0–1.0). Lower = more memories recalled.</div>
      </div>
      <div class="wfield">
        <label>Max memory tokens</label>
        <input type="number" id="mem-max-tokens" min="0" max="8000" step="100"
               value="${escHtml(String(wizard.memoryMaxTokens))}">
        <div class="wfield-hint">Hard cap on tokens spent on injected memories per request. Set 0 for no app-side cap.</div>
      </div>
      <div class="wfield">
        <label>Retrieval temperature</label>
        <input type="number" id="mem-retrieval-temp" min="0" max="2.0" step="0.1"
               value="${escHtml(String(wizard.retrievalTemperature))}">
        <div class="wfield-hint">0.0 = deterministic top-k recall; higher values introduce randomness in which memories surface.</div>
      </div>
      <div class="wfield">
        <label>Serendipity budget (fraction)</label>
        <input type="number" id="mem-serendipity" min="0" max="1.0" step="0.05"
               value="${escHtml(String(wizard.serendipityBudget))}">
        <div class="wfield-hint">Fraction of memory token budget filled with random memories. 0.0 = disabled. Encourages surfacing forgotten context.</div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Memory Decay</h3>
      <p class="wdesc">Older memories can be down-weighted using exponential decay.</p>
      <div class="wfield">
        <label>Decay rate (λ)</label>
        <input type="number" id="mem-decay-rate" min="0" max="1.0" step="0.01"
               value="${escHtml(String(wizard.memoryDecayRate))}">
        <div class="wfield-hint">score × e^(−λ × age_days). 0.0 = no decay; 0.03 ≈ half-life 23 days; 0.1 ≈ half-life 7 days.</div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Auto-Extraction</h3>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Enable auto-extraction</span>
          <span class="connector-desc">Regex-based fact extraction after each turn (zero extra LLM calls)</span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="mem-auto-extract" ${wizard.autoExtract ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>
    </div>
  `;

  const initBtn = document.getElementById("mem-init-workspace-btn");
  if (initBtn) {
    initBtn.addEventListener("click", () => {
      const pathEl = document.getElementById("mem-workspace-dir");
      const customPath = pathEl ? pathEl.value.trim() : "";
      const statusEl = document.getElementById("mem-init-ws-status");
      if (statusEl) statusEl.textContent = "Initializing…";
      initBtn.disabled = true;
      send({ type: "init_workspace", path: customPath });
    });
  }
}

// ── Integrations tab ─────────────────────────────────────────────────────────

const EMAIL_PROVIDERS = [
  { label: "Gmail",           imap_host: "imap.gmail.com",          smtp_host: "smtp.gmail.com",          imap_port: 993, smtp_port: 587 },
  { label: "Outlook/Hotmail", imap_host: "outlook.office365.com",   smtp_host: "smtp.office365.com",      imap_port: 993, smtp_port: 587 },
  { label: "iCloud",          imap_host: "imap.mail.me.com",        smtp_host: "smtp.mail.me.com",        imap_port: 993, smtp_port: 587 },
  { label: "QQ Mail",         imap_host: "imap.qq.com",             smtp_host: "smtp.qq.com",             imap_port: 993, smtp_port: 587 },
  { label: "163 Mail",        imap_host: "imap.163.com",            smtp_host: "smtp.163.com",            imap_port: 993, smtp_port: 25  },
  { label: "Custom",          imap_host: "",                         smtp_host: "",                        imap_port: 993, smtp_port: 587 },
];

const CALDAV_PROVIDERS = [
  { label: "Google Calendar", url: "https://www.google.com/calendar/dav" },
  { label: "iCloud",          url: "https://caldav.icloud.com" },
  { label: "Fastmail",        url: "https://caldav.fastmail.com" },
  { label: "NextCloud",       url: "https://your-server/remote.php/dav" },
  { label: "Custom",          url: "" },
];

export function renderIntegrationsTab() {
  const pwdPlaceholder    = emailCfg.password_set    ? "••••••••  (already set)" : "App password";
  const calPwdPlaceholder = calendarCfg.password_set ? "••••••••  (already set)" : "App password";

  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">📧 Email (IMAP/SMTP)</h3>
      <p class="settings-hint">
        Uses Python stdlib (imaplib/smtplib) — no extra install needed.<br>
        Requires an <strong>App Password</strong>, not your account password.<br>
        Gmail: Google Account → Security → 2-Step Verification → App Passwords.<br>
        iCloud: <a href="https://appleid.apple.com" target="_blank" rel="noopener">appleid.apple.com</a> → Sign-In &amp; Security → App-Specific Passwords.
      </p>
      <div class="settings-field">
        <label>Quick-fill provider</label>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
          ${EMAIL_PROVIDERS.map((p, i) => `<button class="chip-btn" data-email-preset="${i}">${p.label}</button>`).join("")}
        </div>
      </div>
      <div class="settings-field">
        <label><input type="checkbox" id="email-enabled" ${emailCfg.enabled ? "checked" : ""}> Enabled</label>
      </div>
      <div class="settings-field">
        <label>Username / Email</label>
        <input id="email-username" type="text" value="${emailCfg.username}" placeholder="you@example.com">
      </div>
      <div class="settings-field">
        <label>App Password</label>
        <input id="email-password" type="password" value="" placeholder="${pwdPlaceholder}">
      </div>
      <div class="settings-row">
        <div class="settings-field">
          <label>IMAP Host</label>
          <input id="email-imap-host" type="text" value="${emailCfg.imap_host}" placeholder="imap.gmail.com">
        </div>
        <div class="settings-field" style="flex:0 0 90px">
          <label>Port</label>
          <input id="email-imap-port" type="number" value="${emailCfg.imap_port}" min="1" max="65535">
        </div>
      </div>
      <div class="settings-row">
        <div class="settings-field">
          <label>SMTP Host</label>
          <input id="email-smtp-host" type="text" value="${emailCfg.smtp_host}" placeholder="smtp.gmail.com">
        </div>
        <div class="settings-field" style="flex:0 0 90px">
          <label>Port</label>
          <input id="email-smtp-port" type="number" value="${emailCfg.smtp_port}" min="1" max="65535">
        </div>
      </div>
      <div class="settings-field">
        <label>Default Mailbox</label>
        <input id="email-mailbox" type="text" value="${emailCfg.mailbox}" placeholder="INBOX">
      </div>
      <p class="settings-hint">Add to <code>tools.enabled</code> in TOML: <code>list_emails</code>, <code>read_email</code>, <code>send_email</code>, <code>search_emails</code>, <code>mark_email_read</code>, <code>move_email</code></p>
      <div class="settings-field" style="margin-top:10px">
        <button id="btn-test-email" class="chip-btn">Test Connection</button>
        <span id="test-email-status" style="margin-left:10px;font-size:12px"></span>
      </div>
      <div id="test-email-log" style="display:none;margin-top:8px;padding:8px;background:var(--bg-code,#1a1a2e);border-radius:6px;font-size:11px;font-family:monospace;white-space:pre-wrap;max-height:120px;overflow-y:auto"></div>
    </div>

    <div class="settings-section">
      <h3 class="settings-section-h">📅 Calendar (CalDAV)</h3>
      <p class="settings-hint">
        Requires <code>pip install caldav&gt;=1.3</code> or <code>pip install hushclaw[calendar]</code>.<br>
        Use an App Password for Google/iCloud (same setup as email above).
      </p>
      <div class="settings-field">
        <label>Quick-fill provider</label>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
          ${CALDAV_PROVIDERS.map((p, i) => `<button class="chip-btn" data-cal-preset="${i}">${p.label}</button>`).join("")}
        </div>
      </div>
      <div class="settings-field">
        <label><input type="checkbox" id="calendar-enabled" ${calendarCfg.enabled ? "checked" : ""}> Enabled</label>
      </div>
      <div class="settings-field">
        <label>CalDAV URL</label>
        <input id="calendar-url" type="text" value="${calendarCfg.url}" placeholder="https://www.google.com/calendar/dav">
      </div>
      <div class="settings-field">
        <label>Username</label>
        <input id="calendar-username" type="text" value="${calendarCfg.username}" placeholder="you@gmail.com">
      </div>
      <div class="settings-field">
        <label>App Password</label>
        <input id="calendar-password" type="password" value="" placeholder="${calPwdPlaceholder}">
      </div>
      <div class="settings-field">
        <label>Calendar Name <span class="settings-hint">(leave empty for all)</span></label>
        <input id="calendar-name" type="text" value="${calendarCfg.calendar_name}" placeholder="My Calendar">
      </div>
      <p class="settings-hint">Add to <code>tools.enabled</code>: <code>list_calendars</code>, <code>list_events</code>, <code>get_event</code>, <code>create_event</code>, <code>delete_event</code></p>
      <div class="settings-field" style="margin-top:10px">
        <button id="btn-test-calendar" class="chip-btn">Test Connection</button>
        <span id="test-calendar-status" style="margin-left:10px;font-size:12px"></span>
      </div>
      <div id="test-calendar-log" style="display:none;margin-top:8px;padding:8px;background:var(--bg-code,#1a1a2e);border-radius:6px;font-size:11px;font-family:monospace;white-space:pre-wrap;max-height:120px;overflow-y:auto"></div>
    </div>

    <div class="settings-section">
      <h3 class="settings-section-h">🍎 macOS Native (Mail.app &amp; Calendar.app)</h3>
      <p class="settings-hint">
        Zero configuration — uses your system's logged-in accounts automatically.<br>
        Available only on macOS. Tools: <code>macos_list_emails</code>, <code>macos_send_email</code>,
        <code>macos_list_calendars</code>, <code>macos_list_events</code>, <code>macos_create_calendar_event</code>.
      </p>
    </div>
  `;

  document.querySelectorAll("[data-email-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = EMAIL_PROVIDERS[parseInt(btn.dataset.emailPreset)];
      if (!p) return;
      document.getElementById("email-imap-host").value = p.imap_host;
      document.getElementById("email-imap-port").value = p.imap_port;
      document.getElementById("email-smtp-host").value = p.smtp_host;
      document.getElementById("email-smtp-port").value = p.smtp_port;
    });
  });

  document.querySelectorAll("[data-cal-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = CALDAV_PROVIDERS[parseInt(btn.dataset.calPreset)];
      if (!p) return;
      document.getElementById("calendar-url").value = p.url;
    });
  });

  document.getElementById("btn-test-email")?.addEventListener("click", () => {
    const log = document.getElementById("test-email-log");
    const status = document.getElementById("test-email-status");
    log.textContent = "";
    log.style.display = "block";
    status.textContent = "Testing…";
    status.style.color = "";
    send({
      type: "test_email",
      imap_host: document.getElementById("email-imap-host")?.value || "",
      imap_port: document.getElementById("email-imap-port")?.value || 993,
      smtp_host: document.getElementById("email-smtp-host")?.value || "",
      smtp_port: document.getElementById("email-smtp-port")?.value || 587,
      username:  document.getElementById("email-username")?.value  || "",
      password:  document.getElementById("email-password")?.value  || "",
    });
  });

  document.getElementById("btn-test-calendar")?.addEventListener("click", () => {
    const log = document.getElementById("test-calendar-log");
    const status = document.getElementById("test-calendar-status");
    log.textContent = "";
    log.style.display = "block";
    status.textContent = "Testing…";
    status.style.color = "";
    send({
      type: "test_calendar",
      url:           document.getElementById("calendar-url")?.value      || "",
      username:      document.getElementById("calendar-username")?.value  || "",
      password:      document.getElementById("calendar-password")?.value  || "",
      calendar_name: document.getElementById("calendar-name")?.value      || "",
    });
  });

}

export function handleTestIntegrationStep(data) {
  const logId = `test-${data.target}-log`;
  const log = document.getElementById(logId);
  if (!log) return;
  const prefix = data.ok === false ? "✗ " : "• ";
  log.textContent += prefix + data.message + "\n";
  log.scrollTop = log.scrollHeight;
}

export function handleTestIntegrationResult(data) {
  const statusId = `test-${data.target}-status`;
  const status = document.getElementById(statusId);
  if (!status) return;
  status.textContent = data.ok ? "✓ Connected" : "✗ Failed";
  status.style.color = data.ok ? "var(--color-success, #4caf50)" : "var(--color-error, #f44336)";
}
