/**
 * settings/handlers.js — handleConfigStatus, handleConfigSaved, resetWizardTimers.
 * Owns no UI state — delegates timer clearing to save.js and transsion.js.
 */

import {
  wizard, connectors, browser,
  emailAccounts, calendarAccounts,
  _defaultEmailAccount, _defaultCalendarAccount,
  setCurrentEmailTab, setCurrentCalendarTab,
  els, send, clearCurrentSessionId,
} from "../state.js";
import { providerById } from "./providers.js";
import { getTheme, getThemeMode } from "../theme.js";
import { resetChatSessionUiState } from "../chat.js";
import { maybeAutoCheckUpdates } from "../updates.js";
import { setTxFromConfig, clearTestTimer } from "./transsion.js";
import { openWizard, renderSettingsModal } from "./tab-misc.js";
import { checkCalendarTimezone } from "../calendar.js";
import { clearWizardSaveTimer } from "./save.js";

// ── Timer reset (called on WS reconnect) ─────────────────────────────────────

export function resetWizardTimers() {
  clearWizardSaveTimer();
  clearTestTimer();
}

// ── Config status handler ─────────────────────────────────────────────────────

export function handleConfigStatus(cfg) {
  wizard.serverConfig = cfg;
  wizard.connectorStatus = cfg.connector_status || {};
  window.__HUSHCLAW_PUBLIC_BASE_URL = cfg.public_base_url || "";

  // Update the header version badge without overwriting the brand/logo DOM.
  const versionEl = document.getElementById("header-logo-version");
  if (versionEl) {
    const ver = cfg.version ? `v${cfg.version}` : "";
    const bt = cfg.build_time ? String(cfg.build_time) : "";
    const text = [ver, bt].filter(Boolean).join(" · ");
    versionEl.textContent = text;
    versionEl.classList.toggle("hidden", !text);
    versionEl.title = text || "";
  }

  if (!wizard.open || wizard._pendingRefresh) {
    wizard._pendingRefresh = false;
    const prov = providerById(cfg.provider);
    wizard.provider      = prov.id;
    wizard.model         = cfg.model || prov.defaultModel;
    wizard.cheapModel    = cfg.cheap_model || "";
    wizard.baseUrl       = cfg.base_url || prov.defaultBaseUrl || "";
    wizard.apiKey        = "";
    wizard.maxTokens     = cfg.max_tokens     ?? 4096;
    wizard.maxToolRounds = cfg.max_tool_rounds ?? 40;
    wizard.systemPrompt  = cfg.system_prompt  || "";
    wizard.costIn        = cfg.cost_per_1k_input_tokens  || 0.0;
    wizard.costOut       = cfg.cost_per_1k_output_tokens || 0.0;

    const txn = cfg.transsion || {};
    setTxFromConfig(txn.email, txn.display_name, txn.access_token, txn.authed);

    const ctx = cfg.context || {};
    wizard.historyBudget        = ctx.history_budget        ?? 80000;
    wizard.compactThreshold     = ctx.compact_threshold     ?? 0.9;
    wizard.compactKeepTurns     = ctx.compact_keep_turns    ?? 6;
    wizard.compactStrategy      = ctx.compact_strategy      || "lossless";
    wizard.memoryMinScore       = ctx.memory_min_score      ?? 0.18;
    wizard.memoryMaxTokens      = ctx.memory_max_tokens     ?? 2500;
    wizard.autoExtract          = ctx.auto_extract          ?? true;
    wizard.memoryDecayRate      = ctx.memory_decay_rate     ?? 0.0;
    wizard.retrievalTemperature = ctx.retrieval_temperature ?? 0.0;
    wizard.serendipityBudget    = ctx.serendipity_budget    ?? 0.0;

    wizard.systemSkillDir = cfg.skill_dir      || "";
    wizard.userSkillDir   = cfg.user_skill_dir || "";
    wizard.toolsProfile   = cfg.tools_profile  || "";
    wizard.workspaceDir   = cfg.workspace_dir  || "";
    wizard.workspaceStatus = cfg.workspace || { configured: false, path: "", soul_md: false, user_md: false };
    wizard.workspacesList  = Array.isArray(cfg.workspaces) ? cfg.workspaces : [];
    wizard.theme     = getTheme();
    wizard.themeMode = getThemeMode();

    const upd = cfg.update || {};
    wizard.updateAutoCheckEnabled    = upd.auto_check_enabled    ?? true;
    wizard.updateCheckIntervalHours  = upd.check_interval_hours  ?? 24;
    wizard.updateChannel             = upd.channel               || "stable";
    wizard.updateCurrentVersion      = upd.current_version       || "";
    wizard.updateLatestVersion       = upd.latest_version        || "";
    wizard.updateAvailable           = Boolean(upd.update_available);
    wizard.updateReleaseUrl          = upd.release_url           || "";
    wizard.updateLastCheckedAt       = Math.max(
      Number(upd.last_checked_at    || 0),
      Number(wizard.updateLastCheckedAt || 0),
    );

    if (wizard.open) renderSettingsModal();
  }

  if (cfg.connectors) {
    const tg = cfg.connectors.telegram || {};
    connectors.telegram.enabled         = Boolean(tg.enabled);
    connectors.telegram.bot_token       = "";
    connectors.telegram.bot_token_set   = Boolean(tg.bot_token_set);
    connectors.telegram.agent           = tg.agent || "default";
    connectors.telegram.workspace       = tg.workspace || "";
    connectors.telegram.allowlist       = (tg.allowlist || []).join(", ");
    connectors.telegram.group_allowlist = (tg.group_allowlist || []).join(", ");
    connectors.telegram.group_policy    = tg.group_policy || "allowlist";
    connectors.telegram.require_mention = Boolean(tg.require_mention);
    connectors.telegram.stream          = tg.stream !== false;
    connectors.telegram.markdown        = tg.markdown !== false;

    const fs = cfg.connectors.feishu || {};
    connectors.feishu.enabled                = Boolean(fs.enabled);
    connectors.feishu.app_id                 = fs.app_id || "";
    connectors.feishu.app_secret             = "";
    connectors.feishu.app_secret_set         = Boolean(fs.app_secret_set);
    connectors.feishu.encrypt_key            = "";
    connectors.feishu.encrypt_key_set        = Boolean(fs.encrypt_key_set);
    connectors.feishu.verification_token     = "";
    connectors.feishu.verification_token_set = Boolean(fs.verification_token_set);
    connectors.feishu.agent                  = fs.agent || "default";
    connectors.feishu.workspace              = fs.workspace || "";
    connectors.feishu.allowlist              = (fs.allowlist || []).join(", ");
    connectors.feishu.stream                 = Boolean(fs.stream);
    connectors.feishu.markdown               = fs.markdown !== false;

    const dc = cfg.connectors.discord || {};
    connectors.discord.enabled         = Boolean(dc.enabled);
    connectors.discord.bot_token       = "";
    connectors.discord.bot_token_set   = Boolean(dc.bot_token_set);
    connectors.discord.agent           = dc.agent || "default";
    connectors.discord.workspace       = dc.workspace || "";
    connectors.discord.allowlist       = (dc.allowlist || []).join(", ");
    connectors.discord.guild_allowlist = (dc.guild_allowlist || []).join(", ");
    connectors.discord.require_mention = dc.require_mention !== false;
    connectors.discord.stream          = dc.stream !== false;
    connectors.discord.markdown        = dc.markdown !== false;

    const sl = cfg.connectors.slack || {};
    connectors.slack.enabled       = Boolean(sl.enabled);
    connectors.slack.bot_token     = "";
    connectors.slack.bot_token_set = Boolean(sl.bot_token_set);
    connectors.slack.app_token     = "";
    connectors.slack.app_token_set = Boolean(sl.app_token_set);
    connectors.slack.agent         = sl.agent || "default";
    connectors.slack.workspace     = sl.workspace || "";
    connectors.slack.allowlist     = (sl.allowlist || []).join(", ");
    connectors.slack.stream        = sl.stream !== false;
    connectors.slack.markdown      = sl.markdown !== false;

    const dt = cfg.connectors.dingtalk || {};
    connectors.dingtalk.enabled           = Boolean(dt.enabled);
    connectors.dingtalk.client_id         = dt.client_id || "";
    connectors.dingtalk.client_secret     = "";
    connectors.dingtalk.client_secret_set = Boolean(dt.client_secret_set);
    connectors.dingtalk.agent             = dt.agent || "default";
    connectors.dingtalk.workspace         = dt.workspace || "";
    connectors.dingtalk.allowlist         = (dt.allowlist || []).join(", ");
    connectors.dingtalk.stream            = dt.stream !== false;
    connectors.dingtalk.markdown          = dt.markdown !== false;

    const wc = cfg.connectors.wecom || {};
    connectors.wecom.enabled         = Boolean(wc.enabled);
    connectors.wecom.corp_id         = wc.corp_id || "";
    connectors.wecom.corp_secret     = "";
    connectors.wecom.corp_secret_set = Boolean(wc.corp_secret_set);
    connectors.wecom.agent_id        = wc.agent_id || 0;
    connectors.wecom.token           = "";
    connectors.wecom.token_set       = Boolean(wc.token_set);
    connectors.wecom.agent           = wc.agent || "default";
    connectors.wecom.workspace       = wc.workspace || "";
    connectors.wecom.allowlist       = (wc.allowlist || []).join(", ");
    connectors.wecom.markdown        = wc.markdown !== false;
  }

  if (cfg.browser) {
    browser.enabled              = cfg.browser.enabled              ?? true;
    browser.headless             = cfg.browser.headless             ?? true;
    browser.timeout              = cfg.browser.timeout              ?? 30;
    browser.playwright_installed = cfg.browser.playwright_installed ?? false;
    browser.use_user_chrome      = cfg.browser.use_user_chrome      ?? false;
    browser.remote_debugging_url = cfg.browser.remote_debugging_url ?? "";
  }

  if (cfg.email) {
    const arr = Array.isArray(cfg.email) ? cfg.email : [cfg.email];
    emailAccounts.length = 0;
    for (const a of arr) {
      emailAccounts.push({
        label:        a.label        || "",
        enabled:      Boolean(a.enabled),
        imap_host:    a.imap_host    || "",
        imap_port:    a.imap_port    || 993,
        smtp_host:    a.smtp_host    || "",
        smtp_port:    a.smtp_port    || 587,
        username:     a.username     || "",
        password:     "",
        password_set: Boolean(a.password_set),
        mailbox:      a.mailbox      || "INBOX",
      });
    }
    if (emailAccounts.length === 0) emailAccounts.push(_defaultEmailAccount());
    setCurrentEmailTab(0);
  }

  if (cfg.calendar) {
    const arr = Array.isArray(cfg.calendar) ? cfg.calendar : [cfg.calendar];
    calendarAccounts.length = 0;
    for (const a of arr) {
      calendarAccounts.push({
        label:         a.label         || "",
        enabled:       Boolean(a.enabled),
        url:           a.url           || "",
        username:      a.username      || "",
        password:      "",
        password_set:  Boolean(a.password_set),
        calendar_name: a.calendar_name || "",
        timezone:      a.timezone      || "",
      });
    }
    if (calendarAccounts.length === 0) calendarAccounts.push(_defaultCalendarAccount());
    setCurrentCalendarTab(0);
    checkCalendarTimezone();
  }

  if (!cfg.configured && !wizard.open) {
    openWizard(false);
  }
  maybeAutoCheckUpdates(cfg);
}

// ── Config saved handler ──────────────────────────────────────────────────────

export function handleConfigSaved(data) {
  console.info(
    "[hushclaw:save] config_saved ok=%s save_client_id=%s error=%s",
    data.ok,
    data.save_client_id ?? "(none)",
    data.error || "",
  );

  // Silent auto-detect saves (tz_autodetect_*) must not affect wizard UI or
  // session state — they are background-only saves with no user interaction.
  const isSilent = String(data.save_client_id ?? "").startsWith("tz_autodetect_");
  if (isSilent) return;

  clearWizardSaveTimer();
  wizard.saving = false;
  els.wbtnSave.disabled = false;
  els.wbtnSave.textContent = "💾 Save";

  if (data.ok) {
    wizard.savedOnce = true;
    els.wbtnClose.style.display = "";
    els.wstatus.textContent = "✓ Saved";
    els.wstatus.className = "wstatus ok";
    clearCurrentSessionId();
    resetChatSessionUiState();
    setTimeout(() => {
      els.wstatus.textContent = "";
      els.wstatus.className = "wstatus";
      send({ type: "get_config_status" });
    }, 3000);
  } else {
    els.wstatus.textContent = "✗ " + (data.error || "Save failed");
    els.wstatus.className = "wstatus err";
  }
}
