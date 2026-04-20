/**
 * settings/save.js — syncFormToState, validateSettings, saveSettings.
 * Owns _wizardSaveTimer so handlers.js can call clearWizardSaveTimer().
 */

import {
  state, wizard, connectors, browser, emailCfg, calendarCfg,
  els, send, escHtml,
} from "../state.js";
import { providerById } from "./providers.js";
import { getTxForSave } from "./transsion.js";

// ── Save timer (exported so handlers.js can clear on reconnect) ─────────────
let _wizardSaveTimer = null;

export function clearWizardSaveTimer() {
  clearTimeout(_wizardSaveTimer);
  _wizardSaveTimer = null;
}

// ── Form → state sync ───────────────────────────────────────────────────────

export function syncFormToState() {
  const apikeyEl    = document.getElementById("wiz-apikey");
  const burlEl      = document.getElementById("wiz-baseurl");
  const modelEl     = document.getElementById("wiz-model");
  const modelSelEl  = document.getElementById("wiz-model-select");
  if (apikeyEl) wizard.apiKey  = apikeyEl.value.trim();
  if (burlEl)   wizard.baseUrl = burlEl.value.trim();
  if (modelSelEl && modelSelEl.style.display !== "none") {
    wizard.model = modelSelEl.value;
  } else if (modelEl) {
    wizard.model = modelEl.value.trim();
  }

  function _fv(id) { const el = document.getElementById(id); return el ? el.value.trim() : ""; }
  function _fc(id, fallback) { const el = document.getElementById(id); return el ? el.checked : fallback; }

  if (document.getElementById("telegram-enabled")) {
    const c = connectors.telegram;
    c.enabled         = _fc("telegram-enabled", c.enabled);
    c.bot_token       = _fv("tg-token");
    c.agent           = _fv("tg-agent") || "default";
    c.workspace       = _fv("tg-workspace");
    c.allowlist       = _fv("tg-allowlist");
    c.group_allowlist = _fv("tg-group-allowlist");
    c.group_policy    = _fv("tg-group-policy") || "allowlist";
    c.require_mention = _fc("tg-require-mention", c.require_mention);
    c.stream          = _fc("tg-stream", c.stream);
    c.markdown        = _fc("tg-markdown", c.markdown);
  }
  if (document.getElementById("feishu-enabled")) {
    const c = connectors.feishu;
    c.enabled             = _fc("feishu-enabled", c.enabled);
    c.app_id              = _fv("fs-appid");
    c.app_secret          = _fv("fs-secret");
    c.encrypt_key         = _fv("fs-encrypt-key");
    c.verification_token  = _fv("fs-verify-token");
    c.agent               = _fv("fs-agent") || "default";
    c.workspace           = _fv("fs-workspace");
    c.allowlist           = _fv("fs-allowlist");
    c.stream              = _fc("fs-stream", c.stream);
    c.markdown            = _fc("fs-markdown", c.markdown);
  }
  if (document.getElementById("discord-enabled")) {
    const c = connectors.discord;
    c.enabled         = _fc("discord-enabled", c.enabled);
    c.bot_token       = _fv("dc-token");
    c.agent           = _fv("dc-agent") || "default";
    c.workspace       = _fv("dc-workspace");
    c.allowlist       = _fv("dc-allowlist");
    c.guild_allowlist = _fv("dc-guild-allowlist");
    c.require_mention = _fc("dc-require-mention", c.require_mention);
    c.stream          = _fc("dc-stream", c.stream);
    c.markdown        = _fc("dc-markdown", c.markdown);
  }
  if (document.getElementById("slack-enabled")) {
    const c = connectors.slack;
    c.enabled    = _fc("slack-enabled", c.enabled);
    c.bot_token  = _fv("sl-bot-token");
    c.app_token  = _fv("sl-app-token");
    c.agent      = _fv("sl-agent") || "default";
    c.workspace  = _fv("sl-workspace");
    c.allowlist  = _fv("sl-allowlist");
    c.stream     = _fc("sl-stream", c.stream);
    c.markdown   = _fc("sl-markdown", c.markdown);
  }
  if (document.getElementById("dingtalk-enabled")) {
    const c = connectors.dingtalk;
    c.enabled       = _fc("dingtalk-enabled", c.enabled);
    c.client_id     = _fv("dt-client-id");
    c.client_secret = _fv("dt-client-secret");
    c.agent         = _fv("dt-agent") || "default";
    c.workspace     = _fv("dt-workspace");
    c.allowlist     = _fv("dt-allowlist");
    c.markdown      = _fc("dt-markdown", c.markdown);
  }
  if (document.getElementById("wecom-enabled")) {
    const c = connectors.wecom;
    c.enabled     = _fc("wecom-enabled", c.enabled);
    c.corp_id     = _fv("wc-corp-id");
    c.corp_secret = _fv("wc-corp-secret");
    c.agent_id    = parseInt(document.getElementById("wc-agent-id")?.value || "0") || 0;
    c.token       = _fv("wc-token");
    c.agent       = _fv("wc-agent") || "default";
    c.workspace   = _fv("wc-workspace");
    c.allowlist   = _fv("wc-allowlist");
    c.markdown    = _fc("wc-markdown", c.markdown);
  }

  const maxTokEl    = document.getElementById("sys-max-tokens");
  const cheapModelEl    = document.getElementById("sys-cheap-model");
  const cheapModelSelEl = document.getElementById("wiz-cheap-model-select");
  const maxRndEl    = document.getElementById("sys-max-tool-rounds");
  const syspromptEl = document.getElementById("sys-system-prompt");
  const costInEl    = document.getElementById("sys-cost-in");
  const costOutEl   = document.getElementById("sys-cost-out");
  const themeModeEl  = document.querySelector('input[name="ui-theme-mode"]:checked');
  const themePickEl  = document.querySelector('[data-theme-pick].active');
  if (maxTokEl) {
    const v = parseInt(maxTokEl.value, 10);
    if (!Number.isNaN(v)) wizard.maxTokens = v;
  }
  if (cheapModelSelEl && cheapModelSelEl.style.display !== "none") {
    wizard.cheapModel = cheapModelSelEl.value;
  } else if (cheapModelEl) {
    wizard.cheapModel = cheapModelEl.value.trim();
  }
  if (maxRndEl) {
    const v = parseInt(maxRndEl.value, 10);
    if (!Number.isNaN(v)) wizard.maxToolRounds = v;
  }
  if (syspromptEl) wizard.systemPrompt  = syspromptEl.value;
  if (costInEl)    wizard.costIn        = parseFloat(costInEl.value)  || 0.0;
  if (costOutEl)   wizard.costOut       = parseFloat(costOutEl.value) || 0.0;
  if (themeModeEl) wizard.themeMode = themeModeEl.value;
  if (themePickEl) wizard.theme     = themePickEl.dataset.themePick;
  const updAutoEl = document.getElementById("upd-auto-check");
  const updIntEl = document.getElementById("upd-interval-hours");
  const updChannelEl = document.getElementById("upd-channel");
  if (updAutoEl) wizard.updateAutoCheckEnabled = updAutoEl.checked;
  if (updIntEl) {
    const v = parseInt(updIntEl.value, 10);
    if (!Number.isNaN(v)) wizard.updateCheckIntervalHours = v;
  }
  if (updChannelEl) wizard.updateChannel = updChannelEl.value || "stable";

  const brEnabledEl = document.getElementById("br-enabled");
  if (brEnabledEl) {
    browser.enabled          = brEnabledEl.checked;
    browser.headless         = document.getElementById("br-headless")?.checked ?? browser.headless;
    browser.timeout          = parseInt(document.getElementById("br-timeout")?.value) || browser.timeout;
    browser.use_user_chrome  = document.getElementById("br-use-user-chrome")?.checked ?? browser.use_user_chrome;
    const cdpUrlEl           = document.getElementById("br-cdp-url");
    if (cdpUrlEl && cdpUrlEl.value.trim()) {
      browser.remote_debugging_url = cdpUrlEl.value.trim();
    }
  }

  const userSkillDirEl = document.getElementById("sys-user-skill-dir");
  if (userSkillDirEl) wizard.userSkillDir = userSkillDirEl.value.trim();

  const wsDirEl    = document.getElementById("sys-workspace-dir");
  const profileEl  = document.getElementById("sys-tools-profile");
  if (wsDirEl)   wizard.workspaceDir = wsDirEl.value.trim();
  if (profileEl) wizard.toolsProfile = profileEl.value;

  function _fnum(id, fallback) { const el = document.getElementById(id); return el ? (parseFloat(el.value) || 0) : fallback; }
  function _fint(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const v = parseInt(el.value, 10);
    return Number.isNaN(v) ? fallback : v;
  }
  function _fsel(id, fallback) { const el = document.getElementById(id); return el ? el.value : fallback; }
  function _fchk(id, fallback) { const el = document.getElementById(id); return el ? el.checked : fallback; }
  if (document.getElementById("mem-history-budget")) {
    wizard.historyBudget        = _fint("mem-history-budget",     wizard.historyBudget);
    wizard.compactThreshold     = _fnum("mem-compact-threshold",  wizard.compactThreshold);
    wizard.compactKeepTurns     = _fint("mem-compact-keep-turns", wizard.compactKeepTurns);
    wizard.compactStrategy      = _fsel("mem-compact-strategy",   wizard.compactStrategy);
    wizard.memoryMinScore       = _fnum("mem-min-score",          wizard.memoryMinScore);
    wizard.memoryMaxTokens      = _fint("mem-max-tokens",         wizard.memoryMaxTokens);
    wizard.retrievalTemperature = _fnum("mem-retrieval-temp",     wizard.retrievalTemperature);
    wizard.serendipityBudget    = _fnum("mem-serendipity",        wizard.serendipityBudget);
    wizard.memoryDecayRate      = _fnum("mem-decay-rate",         wizard.memoryDecayRate);
    wizard.autoExtract          = _fchk("mem-auto-extract",       wizard.autoExtract);
    const memWsDirEl = document.getElementById("mem-workspace-dir");
    if (memWsDirEl) wizard.workspaceDir = memWsDirEl.value.trim();
  }

  if (document.getElementById("email-enabled")) {
    emailCfg.enabled   = document.getElementById("email-enabled").checked;
    emailCfg.username  = (document.getElementById("email-username")?.value || "").trim();
    const epwd = (document.getElementById("email-password")?.value || "").trim();
    if (epwd) emailCfg.password = epwd;
    emailCfg.imap_host = (document.getElementById("email-imap-host")?.value || "").trim();
    emailCfg.imap_port = parseInt(document.getElementById("email-imap-port")?.value) || emailCfg.imap_port;
    emailCfg.smtp_host = (document.getElementById("email-smtp-host")?.value || "").trim();
    emailCfg.smtp_port = parseInt(document.getElementById("email-smtp-port")?.value) || emailCfg.smtp_port;
    emailCfg.mailbox   = (document.getElementById("email-mailbox")?.value || "INBOX").trim();
  }
  if (document.getElementById("calendar-enabled")) {
    calendarCfg.enabled       = document.getElementById("calendar-enabled").checked;
    calendarCfg.url           = (document.getElementById("calendar-url")?.value      || "").trim();
    calendarCfg.username      = (document.getElementById("calendar-username")?.value || "").trim();
    const cpwd = (document.getElementById("calendar-password")?.value || "").trim();
    if (cpwd) calendarCfg.password = cpwd;
    calendarCfg.calendar_name = (document.getElementById("calendar-name")?.value     || "").trim();
    calendarCfg.timezone      = (document.getElementById("calendar-timezone")?.value || "").trim();
  }
}

export function validateSettings() {
  const prov = providerById(wizard.provider);
  if (wizard.provider === "transsion") {
    const hasKey =
      Boolean(wizard.apiKey) ||
      (wizard.serverConfig &&
        wizard.serverConfig.provider === "transsion" &&
        wizard.serverConfig.api_key_set);
    if (!hasKey) {
      return "Sign in with your Transsion email and verification code first, then click Save.";
    }
  }
  if (prov.needsKey) {
    if (wizard.apiKey && /^https?:\/\//i.test(wizard.apiKey)) {
      return "API Key looks like a URL. Paste the key value, not the endpoint URL.";
    }
    const alreadySet =
      wizard.serverConfig &&
      wizard.serverConfig.provider === wizard.provider &&
      wizard.serverConfig.api_key_set;
    if (!wizard.apiKey && !alreadySet) {
      return `${prov.keyLabel} is required. Go to the Model tab to enter it.`;
    }
  }
  return "";
}

export function saveSettings() {
  syncFormToState();

  const validationErr = validateSettings();
  if (validationErr) {
    els.wstatus.textContent = "✗ " + validationErr;
    els.wstatus.className = "wstatus err";
    return;
  }

  const { email: txEmail, displayName: txDisplayName, accessToken: txAccessToken } = getTxForSave();

  const prov    = providerById(wizard.provider);
  const model   = wizard.model || prov.defaultModel;
  const baseUrl = (wizard.baseUrl || "").trim() || prov.defaultBaseUrl;

  function _intList(raw) {
    return (raw || "").split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n));
  }
  function _strList(raw) {
    return (raw || "").split(",").map((s) => s.trim()).filter(Boolean);
  }
  function _al(raw) { return typeof raw === "string" ? raw : (raw || []).join(", "); }

  const tg = connectors.telegram;
  const tgConfig = {
    enabled: tg.enabled, agent: tg.agent || "default",
    workspace: tg.workspace || "",
    allowlist: _intList(_al(tg.allowlist)),
    group_allowlist: _intList(_al(tg.group_allowlist)),
    group_policy: tg.group_policy || "allowlist",
    require_mention: tg.require_mention,
    stream: tg.stream,
    markdown: tg.markdown !== false,
  };
  if (tg.bot_token) tgConfig.bot_token = tg.bot_token;

  const fs = connectors.feishu;
  const fsConfig = {
    enabled: fs.enabled, agent: fs.agent || "default",
    workspace: fs.workspace || "",
    allowlist: _strList(_al(fs.allowlist)), stream: fs.stream,
    markdown: fs.markdown !== false,
  };
  if (fs.app_id)             fsConfig.app_id             = fs.app_id;
  if (fs.app_secret)         fsConfig.app_secret         = fs.app_secret;
  if (fs.encrypt_key)        fsConfig.encrypt_key        = fs.encrypt_key;
  if (fs.verification_token) fsConfig.verification_token = fs.verification_token;

  const dc = connectors.discord;
  const dcConfig = {
    enabled: dc.enabled, agent: dc.agent || "default",
    workspace: dc.workspace || "",
    allowlist: _intList(_al(dc.allowlist)),
    guild_allowlist: _intList(_al(dc.guild_allowlist)),
    require_mention: dc.require_mention, stream: dc.stream,
    markdown: dc.markdown !== false,
  };
  if (dc.bot_token) dcConfig.bot_token = dc.bot_token;

  const sl = connectors.slack;
  const slConfig = {
    enabled: sl.enabled, agent: sl.agent || "default",
    workspace: sl.workspace || "",
    allowlist: _strList(_al(sl.allowlist)), stream: sl.stream,
    markdown: sl.markdown !== false,
  };
  if (sl.bot_token) slConfig.bot_token = sl.bot_token;
  if (sl.app_token) slConfig.app_token = sl.app_token;

  const dt = connectors.dingtalk;
  const dtConfig = {
    enabled: dt.enabled, agent: dt.agent || "default",
    workspace: dt.workspace || "",
    allowlist: _strList(_al(dt.allowlist)), stream: dt.stream,
    markdown: dt.markdown !== false,
  };
  if (dt.client_id)     dtConfig.client_id     = dt.client_id;
  if (dt.client_secret) dtConfig.client_secret = dt.client_secret;

  const wc = connectors.wecom;
  const wcConfig = {
    enabled: wc.enabled, agent: wc.agent || "default",
    workspace: wc.workspace || "",
    agent_id: wc.agent_id || 0,
    allowlist: _strList(_al(wc.allowlist)),
    markdown: wc.markdown !== false,
  };
  if (wc.corp_id)     wcConfig.corp_id     = wc.corp_id;
  if (wc.corp_secret) wcConfig.corp_secret = wc.corp_secret;
  if (wc.token)       wcConfig.token       = wc.token;

  const config = {
    provider: { name: wizard.provider, base_url: baseUrl },
    agent: {
      model,
      cheap_model:     wizard.cheapModel || "",
      max_tokens:      wizard.maxTokens,
      max_tool_rounds: wizard.maxToolRounds,
    },
    context: {
      history_budget:        wizard.historyBudget,
      compact_threshold:     wizard.compactThreshold,
      compact_keep_turns:    wizard.compactKeepTurns,
      compact_strategy:      wizard.compactStrategy,
      memory_min_score:      wizard.memoryMinScore,
      memory_max_tokens:     wizard.memoryMaxTokens,
      auto_extract:          wizard.autoExtract,
      memory_decay_rate:     wizard.memoryDecayRate,
      retrieval_temperature: wizard.retrievalTemperature,
      serendipity_budget:    wizard.serendipityBudget,
    },
    update: {
      auto_check_enabled: wizard.updateAutoCheckEnabled,
      check_interval_hours: wizard.updateCheckIntervalHours,
      channel: wizard.updateChannel || "stable",
      last_checked_at: wizard.updateLastCheckedAt || 0,
    },
    connectors: {
      telegram: tgConfig, feishu: fsConfig,
      discord: dcConfig, slack: slConfig,
      dingtalk: dtConfig, wecom: wcConfig,
    },
    browser: {
      enabled:                browser.enabled,
      headless:               browser.headless,
      timeout:                browser.timeout,
      use_user_chrome:        browser.use_user_chrome,
      remote_debugging_url:   browser.remote_debugging_url,
    },
    email: {
      enabled:   emailCfg.enabled,
      imap_host: emailCfg.imap_host,
      imap_port: emailCfg.imap_port,
      smtp_host: emailCfg.smtp_host,
      smtp_port: emailCfg.smtp_port,
      username:  emailCfg.username,
      mailbox:   emailCfg.mailbox,
      ...(emailCfg.password ? { password: emailCfg.password } : {}),
    },
    calendar: {
      enabled:       calendarCfg.enabled,
      url:           calendarCfg.url,
      username:      calendarCfg.username,
      calendar_name: calendarCfg.calendar_name,
      timezone:      calendarCfg.timezone,
      ...(calendarCfg.password ? { password: calendarCfg.password } : {}),
    },
  };
  if (wizard.apiKey && (prov.needsKey || wizard.provider === "transsion")) {
    config.provider.api_key = wizard.apiKey;
  }
  if (wizard.provider === "transsion" && txEmail) {
    config.transsion = {
      email:        txEmail,
      display_name: txDisplayName || "",
    };
    if (txAccessToken) {
      config.transsion.access_token = txAccessToken;
    }
  }
  if (wizard.systemPrompt.trim())     config.agent.system_prompt = wizard.systemPrompt.trim();
  config.agent = config.agent || {};
  config.agent.workspace_dir = wizard.workspaceDir || "";
  if (wizard.costIn  > 0) config.provider.cost_per_1k_input_tokens  = wizard.costIn;
  if (wizard.costOut > 0) config.provider.cost_per_1k_output_tokens = wizard.costOut;
  config.tools = {
    user_skill_dir: wizard.userSkillDir || "",
    profile:        wizard.toolsProfile || "",
  };
  config.workspaces = {
    list: (wizard.workspacesList || []).map(ws => ({
      name: ws.name,
      path: ws.path,
      description: ws.description || "",
    })),
  };

  wizard.saving = true;
  els.wbtnSave.disabled = true;
  els.wbtnSave.textContent = "⠸ Saving…";
  els.wstatus.textContent = "";
  els.wstatus.className = "wstatus";

  clearTimeout(_wizardSaveTimer);
  const saveClientId = `sv_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  const savePayload = { type: "save_config", config, save_client_id: saveClientId };
  let payloadJson = "";
  try {
    payloadJson = JSON.stringify(savePayload);
  } catch (err) {
    console.error("[hushclaw:save] JSON.stringify failed", saveClientId, err);
    els.wstatus.textContent = "✗ Could not build save payload (see console).";
    els.wstatus.className = "wstatus err";
    wizard.saving = false;
    els.wbtnSave.disabled = false;
    els.wbtnSave.textContent = "💾 Save";
    return;
  }

  console.info(
    "[hushclaw:save] sending save_client_id=%s bytes=%d ws_readyState=%s",
    saveClientId,
    payloadJson.length,
    state.ws ? state.ws.readyState : "no_ws",
  );

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    console.warn("[hushclaw:save] WebSocket not open — save not sent", saveClientId);
    wizard.saving = false;
    els.wbtnSave.disabled = false;
    els.wbtnSave.textContent = "💾 Save";
    els.wstatus.textContent = "✗ Not connected. Refresh the page and try again.";
    els.wstatus.className = "wstatus err";
    return;
  }

  _wizardSaveTimer = setTimeout(() => {
    _wizardSaveTimer = null;
    if (!wizard.saving) return;
    wizard.saving = false;
    els.wbtnSave.disabled = false;
    els.wbtnSave.textContent = "💾 Save";
    els.wstatus.textContent = "✗ Request timed out. Check your connection and try again.";
    els.wstatus.className = "wstatus err";
    console.warn(
      "[hushclaw:save] TIMEOUT waiting for config_saved save_client_id=%s (see server logs for same id)",
      saveClientId,
    );
  }, 60000);

  state.ws.send(payloadJson);
}
