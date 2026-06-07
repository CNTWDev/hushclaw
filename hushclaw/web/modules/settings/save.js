/**
 * settings/save.js — syncFormToState, validateSettings, saveSettings.
 * Owns _wizardSaveTimer so handlers.js can call clearWizardSaveTimer().
 */

import {
  state, wizard, connectors, appConnectors, browser,
  emailAccounts, calendarAccounts, currentEmailTab, currentCalendarTab,
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
  const timeoutEl   = document.getElementById("wiz-provider-timeout");
  const modelEl     = document.getElementById("wiz-model");
  const modelSelEl  = document.getElementById("wiz-model-select");
  if (apikeyEl) wizard.apiKey  = apikeyEl.value.trim();
  if (burlEl)   wizard.baseUrl = burlEl.value.trim();
  if (timeoutEl) {
    const v = parseInt(timeoutEl.value, 10);
    if (!Number.isNaN(v)) wizard.providerTimeout = v;
  }
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

  if (document.getElementById("app-github-enabled")) {
    const c = appConnectors.github;
    c.enabled      = _fc("app-github-enabled", c.enabled);
    c.auth_mode    = _fv("app-github-auth-mode") || "managed";
    c.auth_type    = _fv("app-github-auth-type") || "pat";
    c.client_id    = _fv("app-github-client-id");
    c.client_id_ref = _fv("app-github-client-id-ref") || "app_connectors.github.client_id";
    c.client_secret = _fv("app-github-client-secret");
    c.client_secret_ref = _fv("app-github-client-secret-ref") || "app_connectors.github.client_secret";
    c.token        = _fv("app-github-token");
    c.token_ref    = _fv("app-github-token-ref") || "app_connectors.github.token";
    c.default_repo = _fv("app-github-default-repo");
    c.allow_actions = _fc("app-github-allow-actions", false);
  }
  if (document.getElementById("app-google-workspace-enabled")) {
    const c = appConnectors.google_workspace;
    c.enabled = _fc("app-google-workspace-enabled", c.enabled);
    c.auth_mode = _fv("app-google-workspace-auth-mode") || "managed";
    c.auth_type = _fv("app-google-workspace-auth-type") || "oauth";
    c.client_id = _fv("app-google-workspace-client-id");
    c.client_id_ref = _fv("app-google-workspace-client-id-ref") || "app_connectors.google_workspace.client_id";
    c.client_secret = _fv("app-google-workspace-client-secret");
    c.client_secret_ref = _fv("app-google-workspace-client-secret-ref") || "app_connectors.google_workspace.client_secret";
    c.access_token = _fv("app-google-workspace-access-token");
    c.access_token_ref = _fv("app-google-workspace-access-token-ref") || "app_connectors.google_workspace.access_token";
    c.refresh_token = _fv("app-google-workspace-refresh-token");
    c.refresh_token_ref = _fv("app-google-workspace-refresh-token-ref") || "app_connectors.google_workspace.refresh_token";
    c.scopes = _fv("app-google-workspace-scopes").split(/\s+/).map((s) => s.trim()).filter(Boolean);
    c.allow_actions = false;
  }
  if (document.getElementById("app-notion-enabled")) {
    const c = appConnectors.notion;
    c.enabled = _fc("app-notion-enabled", c.enabled);
    c.auth_mode = _fv("app-notion-auth-mode") || "managed";
    c.auth_type = _fv("app-notion-auth-type") || "internal_token";
    c.client_id = _fv("app-notion-client-id");
    c.client_id_ref = _fv("app-notion-client-id-ref") || "app_connectors.notion.client_id";
    c.client_secret = _fv("app-notion-client-secret");
    c.client_secret_ref = _fv("app-notion-client-secret-ref") || "app_connectors.notion.client_secret";
    c.token = _fv("app-notion-token");
    c.token_ref = _fv("app-notion-token-ref") || "app_connectors.notion.token";
    c.workspace_name = _fv("app-notion-workspace-name");
    c.allow_actions = false;
  }
  if (document.getElementById("app-jira-enabled")) {
    const c = appConnectors.jira;
    c.enabled = _fc("app-jira-enabled", c.enabled);
    c.auth_mode = _fv("app-jira-auth-mode") || "managed";
    c.auth_type = _fv("app-jira-auth-type") || "api_token";
    c.site_url = _fv("app-jira-site-url");
    c.email = _fv("app-jira-email");
    c.client_id = _fv("app-jira-client-id");
    c.client_id_ref = _fv("app-jira-client-id-ref") || "app_connectors.jira.client_id";
    c.client_secret = _fv("app-jira-client-secret");
    c.client_secret_ref = _fv("app-jira-client-secret-ref") || "app_connectors.jira.client_secret";
    c.token = _fv("app-jira-token");
    c.token_ref = _fv("app-jira-token-ref") || "app_connectors.jira.token";
    c.access_token = _fv("app-jira-access-token");
    c.access_token_ref = _fv("app-jira-access-token-ref") || "app_connectors.jira.access_token";
    c.refresh_token = _fv("app-jira-refresh-token");
    c.refresh_token_ref = _fv("app-jira-refresh-token-ref") || "app_connectors.jira.refresh_token";
    c.cloud_id = _fv("app-jira-cloud-id");
    c.scopes = _fv("app-jira-scopes").split(/\s+/).map((s) => s.trim()).filter(Boolean);
    c.allow_actions = false;
  }
  if (document.getElementById("app-reddit-enabled")) {
    const c = appConnectors.reddit;
    c.enabled = _fc("app-reddit-enabled", c.enabled);
    c.auth_mode = _fv("app-reddit-auth-mode") || "custom";
    c.auth_type = _fv("app-reddit-auth-type") || "oauth";
    c.client_id = _fv("app-reddit-client-id");
    c.client_id_ref = _fv("app-reddit-client-id-ref") || "app_connectors.reddit.client_id";
    c.client_secret = _fv("app-reddit-client-secret");
    c.client_secret_ref = _fv("app-reddit-client-secret-ref") || "app_connectors.reddit.client_secret";
    c.access_token = _fv("app-reddit-access-token");
    c.access_token_ref = _fv("app-reddit-access-token-ref") || "app_connectors.reddit.access_token";
    c.refresh_token = _fv("app-reddit-refresh-token");
    c.refresh_token_ref = _fv("app-reddit-refresh-token-ref") || "app_connectors.reddit.refresh_token";
    c.user_agent = _fv("app-reddit-user-agent") || "HushClaw-AppConnector/1.0";
    c.default_subreddit = _fv("app-reddit-default-subreddit");
    c.allow_actions = _fc("app-reddit-allow-actions", false);
  }
  if (document.getElementById("app-x-enabled")) {
    const c = appConnectors.x;
    c.enabled = _fc("app-x-enabled", c.enabled);
    c.auth_mode = _fv("app-x-auth-mode") || "custom";
    c.auth_type = _fv("app-x-auth-type") || "app_keys";
    c.consumer_key = _fv("app-x-consumer-key");
    c.consumer_key_ref = _fv("app-x-consumer-key-ref") || "app_connectors.x.consumer_key";
    c.consumer_secret = _fv("app-x-consumer-secret");
    c.consumer_secret_ref = _fv("app-x-consumer-secret-ref") || "app_connectors.x.consumer_secret";
    c.bearer_token = _fv("app-x-bearer-token");
    c.bearer_token_ref = _fv("app-x-bearer-token-ref") || "app_connectors.x.bearer_token";
    c.access_token = _fv("app-x-access-token");
    c.access_token_ref = _fv("app-x-access-token-ref") || "app_connectors.x.access_token";
    c.refresh_token = _fv("app-x-refresh-token");
    c.refresh_token_ref = _fv("app-x-refresh-token-ref") || "app_connectors.x.refresh_token";
    c.allow_actions = _fc("app-x-allow-actions", false);
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
  if (syspromptEl && syspromptEl.style.display !== "none") {
    wizard.systemPrompt = syspromptEl.value;
    wizard.systemPromptDefault = false;
  }
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
    wizard.embedProvider        = _fv("mem-embed-provider") || wizard.embedProvider;
    wizard.embedModel           = _fv("mem-embed-model");
    const memWsDirEl = document.getElementById("mem-workspace-dir");
    if (memWsDirEl) wizard.workspaceDir = memWsDirEl.value.trim();
  }

  if (document.getElementById("email-enabled")) {
    const acct = emailAccounts[currentEmailTab];
    if (acct) {
      acct.label    = (document.getElementById("email-label")?.value || "").trim();
      acct.enabled  = document.getElementById("email-enabled").checked;
      acct.username = (document.getElementById("email-username")?.value || "").trim();
      const epwd = (document.getElementById("email-password")?.value || "").trim();
      if (epwd) acct.password = epwd;
      acct.imap_host = (document.getElementById("email-imap-host")?.value || "").trim();
      acct.imap_port = parseInt(document.getElementById("email-imap-port")?.value) || acct.imap_port;
      acct.smtp_host = (document.getElementById("email-smtp-host")?.value || "").trim();
      acct.smtp_port = parseInt(document.getElementById("email-smtp-port")?.value) || acct.smtp_port;
      acct.mailbox   = (document.getElementById("email-mailbox")?.value || "INBOX").trim();
    }
  }
  if (document.getElementById("calendar-enabled")) {
    const acct = calendarAccounts[currentCalendarTab];
    if (acct) {
      acct.label         = (document.getElementById("calendar-label")?.value     || "").trim();
      acct.enabled       = document.getElementById("calendar-enabled").checked;
      acct.url           = (document.getElementById("calendar-url")?.value      || "").trim();
      acct.username      = (document.getElementById("calendar-username")?.value || "").trim();
      const cpwd = (document.getElementById("calendar-password")?.value || "").trim();
      if (cpwd) acct.password = cpwd;
      acct.calendar_name = (document.getElementById("calendar-name")?.value     || "").trim();
    }
  }
  // sys-timezone lives in the System tab; read it whenever it's present.
  const tzVal = (document.getElementById("sys-timezone")?.value || "").trim();
  if (tzVal && calendarAccounts[currentCalendarTab]) {
    calendarAccounts[currentCalendarTab].timezone = tzVal;
  }
}

export function validateSettings() {
  const prov = providerById(wizard.provider);
  if (wizard.provider === "transsion") {
    const tx = getTxForSave();
    const hasKey =
      Boolean(wizard.apiKey) ||
      (wizard.serverConfig &&
        wizard.serverConfig.provider === "transsion" &&
        wizard.serverConfig.api_key_saved);
    const hasSignedInSession = Boolean(tx.accessToken && wizard.apiKey);
    const hasSavedAuthedSession =
      Boolean(wizard.serverConfig &&
        wizard.serverConfig.provider === "transsion" &&
        wizard.serverConfig.transsion &&
        wizard.serverConfig.transsion.authed);
    if (!hasKey) {
      return "Sign in with your Transsion email and verification code first, then click Save.";
    }
    if (!wizard.providerTestOk && !hasSignedInSession && !hasSavedAuthedSession) {
      return "Complete the Transsion sign-in test before saving.";
    }
  }
  if (prov.needsKey) {
    if (wizard.apiKey && /^https?:\/\//i.test(wizard.apiKey)) {
      return "API Key looks like a URL. Paste the key value, not the endpoint URL.";
    }
    const alreadySet =
      wizard.serverConfig &&
      wizard.serverConfig.provider === wizard.provider &&
      wizard.serverConfig.api_key_saved;
    if (!wizard.apiKey && !alreadySet) {
      return `${prov.keyLabel} is required. Go to the Model tab to enter it.`;
    }
    if (!wizard.providerTestOk) {
      return "Test the model connection successfully before saving.";
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

  const gh = appConnectors.github;
  const ghConfig = {
    enabled: gh.enabled,
    auth_mode: gh.auth_mode || "managed",
    auth_type: gh.auth_type || "pat",
    client_id_ref: gh.client_id_ref || "app_connectors.github.client_id",
    client_secret_ref: gh.client_secret_ref || "app_connectors.github.client_secret",
    token_ref: gh.token_ref || "app_connectors.github.token",
    default_repo: gh.default_repo || "",
    allow_actions: false,
  };
  if (gh.client_id) ghConfig.client_id = gh.client_id;
  if (gh.client_secret) ghConfig.client_secret = gh.client_secret;
  if (gh.token) ghConfig.token = gh.token;

  const gw = appConnectors.google_workspace;
  const gwConfig = {
    enabled: gw.enabled,
    auth_mode: gw.auth_mode || "managed",
    auth_type: gw.auth_type || "oauth",
    client_id_ref: gw.client_id_ref || "app_connectors.google_workspace.client_id",
    client_secret_ref: gw.client_secret_ref || "app_connectors.google_workspace.client_secret",
    access_token_ref: gw.access_token_ref || "app_connectors.google_workspace.access_token",
    refresh_token_ref: gw.refresh_token_ref || "app_connectors.google_workspace.refresh_token",
    scopes: gw.scopes || [],
    allow_actions: false,
  };
  if (gw.client_id) gwConfig.client_id = gw.client_id;
  if (gw.client_secret) gwConfig.client_secret = gw.client_secret;
  if (gw.access_token) gwConfig.access_token = gw.access_token;
  if (gw.refresh_token) gwConfig.refresh_token = gw.refresh_token;

  const nt = appConnectors.notion;
  const ntConfig = {
    enabled: nt.enabled,
    auth_mode: nt.auth_mode || "managed",
    auth_type: nt.auth_type || "internal_token",
    client_id_ref: nt.client_id_ref || "app_connectors.notion.client_id",
    client_secret_ref: nt.client_secret_ref || "app_connectors.notion.client_secret",
    token_ref: nt.token_ref || "app_connectors.notion.token",
    workspace_name: nt.workspace_name || "",
    allow_actions: false,
  };
  if (nt.client_id) ntConfig.client_id = nt.client_id;
  if (nt.client_secret) ntConfig.client_secret = nt.client_secret;
  if (nt.token) ntConfig.token = nt.token;

  const jr = appConnectors.jira;
  const jrConfig = {
    enabled: jr.enabled,
    auth_mode: jr.auth_mode || "managed",
    auth_type: jr.auth_type || "api_token",
    site_url: jr.site_url || "",
    email: jr.email || "",
    client_id_ref: jr.client_id_ref || "app_connectors.jira.client_id",
    client_secret_ref: jr.client_secret_ref || "app_connectors.jira.client_secret",
    token_ref: jr.token_ref || "app_connectors.jira.token",
    access_token_ref: jr.access_token_ref || "app_connectors.jira.access_token",
    refresh_token_ref: jr.refresh_token_ref || "app_connectors.jira.refresh_token",
    cloud_id: jr.cloud_id || "",
    scopes: jr.scopes || [],
    allow_actions: false,
  };
  if (jr.client_id) jrConfig.client_id = jr.client_id;
  if (jr.client_secret) jrConfig.client_secret = jr.client_secret;
  if (jr.token) jrConfig.token = jr.token;
  if (jr.access_token) jrConfig.access_token = jr.access_token;
  if (jr.refresh_token) jrConfig.refresh_token = jr.refresh_token;

  const rd = appConnectors.reddit;
  const rdConfig = {
    enabled: rd.enabled,
    auth_mode: rd.auth_mode || "custom",
    auth_type: rd.auth_type || "oauth",
    client_id_ref: rd.client_id_ref || "app_connectors.reddit.client_id",
    client_secret_ref: rd.client_secret_ref || "app_connectors.reddit.client_secret",
    access_token_ref: rd.access_token_ref || "app_connectors.reddit.access_token",
    refresh_token_ref: rd.refresh_token_ref || "app_connectors.reddit.refresh_token",
    user_agent: rd.user_agent || "HushClaw-AppConnector/1.0",
    default_subreddit: rd.default_subreddit || "",
    allow_actions: Boolean(rd.allow_actions),
  };
  if (rd.client_id) rdConfig.client_id = rd.client_id;
  if (rd.client_secret) rdConfig.client_secret = rd.client_secret;
  if (rd.access_token) rdConfig.access_token = rd.access_token;
  if (rd.refresh_token) rdConfig.refresh_token = rd.refresh_token;

  const xc = appConnectors.x;
  const xConfig = {
    enabled: xc.enabled,
    auth_mode: xc.auth_mode || "custom",
    auth_type: xc.auth_type || "app_keys",
    consumer_key_ref: xc.consumer_key_ref || "app_connectors.x.consumer_key",
    consumer_secret_ref: xc.consumer_secret_ref || "app_connectors.x.consumer_secret",
    bearer_token_ref: xc.bearer_token_ref || "app_connectors.x.bearer_token",
    access_token_ref: xc.access_token_ref || "app_connectors.x.access_token",
    refresh_token_ref: xc.refresh_token_ref || "app_connectors.x.refresh_token",
    allow_actions: Boolean(xc.allow_actions),
  };
  if (xc.consumer_key) xConfig.consumer_key = xc.consumer_key;
  if (xc.consumer_secret) xConfig.consumer_secret = xc.consumer_secret;
  if (xc.bearer_token) xConfig.bearer_token = xc.bearer_token;
  if (xc.access_token) xConfig.access_token = xc.access_token;
  if (xc.refresh_token) xConfig.refresh_token = xc.refresh_token;

  const config = {
    provider: { name: wizard.provider, base_url: baseUrl, timeout: wizard.providerTimeout || 360 },
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
    memory: {
      embed_provider: wizard.embedProvider,
      embed_model:    wizard.embedModel,
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
    app_connectors: {
      broker_base_url: appConnectors.broker_base_url || "https://bus-ie.aibotplatform.com/hushclaw/app-connectors/oauth",
      github: ghConfig,
      google_workspace: gwConfig,
      notion: ntConfig,
      jira: jrConfig,
      reddit: rdConfig,
      x: xConfig,
    },
    browser: {
      enabled:                browser.enabled,
      headless:               browser.headless,
      timeout:                browser.timeout,
      use_user_chrome:        browser.use_user_chrome,
      remote_debugging_url:   browser.remote_debugging_url,
    },
    email: emailAccounts.map(a => ({
      label:     a.label,
      enabled:   a.enabled,
      imap_host: a.imap_host,
      imap_port: a.imap_port,
      smtp_host: a.smtp_host,
      smtp_port: a.smtp_port,
      username:  a.username,
      mailbox:   a.mailbox,
      ...(a.password ? { password: a.password } : {}),
    })),
    calendar: calendarAccounts.map(a => ({
      label:         a.label,
      enabled:       a.enabled,
      url:           a.url,
      username:      a.username,
      calendar_name: a.calendar_name,
      timezone:      a.timezone,
      ...(a.password ? { password: a.password } : {}),
    })),
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
  config.agent = config.agent || {};
  const prompt = (wizard.systemPrompt || "").trim();
  if (wizard.systemPromptDefault) {
    if (wizard.systemPromptTouched) config.agent.system_prompt = "";
  } else if (prompt) {
    config.agent.system_prompt = prompt;
  } else if (wizard.systemPromptTouched) {
    config.agent.system_prompt = "";
  }
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
