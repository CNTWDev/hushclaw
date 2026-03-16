/**
 * HushClaw Web UI — app.js
 * Pure JS, no build step, no external dependencies.
 */

"use strict";

// ── Spinner ────────────────────────────────────────────────────────────────

const SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
let _spinIdx = 0;

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  ws: null,
  session_id: null,
  agent: "default",
  agents: [],           // populated from "agents" WS message
  tab: "chat",
  inTokens: 0,
  outTokens: 0,
  sending: false,
  // reconnect
  _reconnectDelay: 1000,
  _reconnectTimer: null,
  // tool bubbles map
  _toolBubbles: {},
  _toolPendingByName: {},
  _toolIndex: 0,
  // current streaming AI bubble
  _aiMsgEl: null,
  _aiBubbleEl: null,
  // thinking indicator
  _thinkingEl: null,
  _thinkingTimer: null,
  _thinkingStart: 0,
  // @mention autocomplete
  _mentionActive: false,
  _mentionIndex: 0,
  _mentionItems: [],
  // sessions sidebar
  _firstSessionLoad: true,
  _activeSessionId: null,
  // file attachments pending in current message
  _attachments: [],
};

// ── Pending-request timers (reset on WS reconnect) ─────────────────────────

let _wizardSaveTimer = null;   // fires if config_saved never arrives
let _testTimer       = null;   // fires if test_provider_result never arrives

// ── Settings modal state ────────────────────────────────────────────────────

const wizard = {
  tab: "model",           // "model" | "channels" | "system" | "memory"
  dismissible: true,      // false = hide Close until after first successful save
  savedOnce: false,       // tracks first successful save for non-dismissible open
  _pendingRefresh: false, // true after settings btn click, triggers form refresh on config_status
  // model tab
  provider: "anthropic-raw",
  apiKey: "",
  baseUrl: "",
  model: "claude-sonnet-4-6",
  // system tab
  maxTokens: 4096,
  maxToolRounds: 10,
  systemPrompt: "",
  costIn: 0.0,
  costOut: 0.0,
  // memory tab
  historyBudget: 60000,
  compactThreshold: 0.85,
  compactKeepTurns: 6,
  compactStrategy: "lossless",
  memoryMinScore: 0.25,
  memoryMaxTokens: 800,
  autoExtract: true,
  memoryDecayRate: 0.0,
  retrievalTemperature: 0.0,
  serendipityBudget: 0.0,
  // meta
  serverConfig: null,
  open: false,
  saving: false,
  saveStatus: { text: "", type: "" },
};

// Provider definitions
const PROVIDERS = [
  {
    id: "anthropic-raw",
    name: "Anthropic / Compatible",
    desc: "Claude models via Anthropic API or any Anthropic-compatible proxy (e.g. AIGOCODE). Uses urllib — no extra deps.",
    needsKey: true,
    defaultModel: "claude-sonnet-4-6",
    modelSuggestions: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    keyLabel: "API Key",
    keyPlaceholder: "sk-ant-api03-…",
    keyHint: 'Anthropic: <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a> &nbsp;·&nbsp; AIGOCODE: use your AIGOCODE dashboard key',
    defaultBaseUrl: "https://api.anthropic.com/v1",
    baseUrlLabel: "Base URL — AIGOCODE proxy: https://api.aigocode.com/v1",
  },
  {
    id: "openai-sdk",
    name: "OpenAI / Compatible",
    desc: "GPT-4o, OpenRouter, Groq, Together, or any OpenAI-compatible endpoint. Uses the official openai SDK.",
    needsKey: true,
    defaultModel: "gpt-4o",
    modelSuggestions: ["gpt-4o", "gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-sonnet-4-6", "google/gemini-pro"],
    keyLabel: "API Key",
    keyPlaceholder: "sk-…",
    keyHint: 'OpenAI: <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com</a> &nbsp;·&nbsp; OpenRouter: <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>',
    defaultBaseUrl: "https://api.openai.com/v1",
    baseUrlLabel: "Base URL (OpenRouter: https://openrouter.ai/api/v1)",
  },
  {
    id: "ollama",
    name: "Ollama (local)",
    desc: "Run models locally via Ollama. No API key required.",
    needsKey: false,
    defaultModel: "llama3.2",
    modelSuggestions: ["llama3.2", "llama3.1", "mistral", "qwen2.5", "phi3"],
    keyLabel: "",
    keyPlaceholder: "",
    keyHint: 'Install Ollama from <a href="https://ollama.ai" target="_blank" rel="noopener">ollama.ai</a>, then run <code>ollama pull llama3.2</code>',
    defaultBaseUrl: "http://localhost:11434",
    baseUrlLabel: "Ollama base URL",
  },
];

function providerById(id) {
  // Normalise legacy / merged provider IDs to their current canonical ID
  const ALIASES = { "openai-raw": "openai-sdk", "anthropic-sdk": "anthropic-raw", "aigocode-raw": "anthropic-raw", "aigocode": "anthropic-raw" };
  const normalised = ALIASES[id] || id;
  return PROVIDERS.find((p) => p.id === normalised) || PROVIDERS[0];
}

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

const els = {
  agentSelect:       $("agent-select"),
  messages:          $("messages"),
  input:             $("input"),
  btnSend:           $("btn-send"),
  btnStop:           $("btn-stop"),
  btnAttach:         $("btn-attach"),
  fileInput:         $("file-input"),
  attachmentChips:   $("attachment-chips"),
  btnHandoverDone:   $("btn-handover-done"),
  handoverBanner:   $("handover-banner"),
  handoverMsg:      $("handover-msg"),
  btnNew:           $("btn-new-session"),
  btnSettings:      $("btn-settings"),
  sessionLabel:     $("session-label"),
  connStatus:       $("conn-status"),
  tokenStats:       $("token-stats"),
  sessionsList:     $("sessions-list"),
  memoriesList:     $("memories-list"),
  memorySearch:     $("memory-search"),
  btnSearchMem:     $("btn-search-memories"),
  btnRefreshMem:    $("btn-refresh-memories"),
  btnRefreshSess:   $("btn-refresh-sessions"),
  // skills
  skillsContent:    $("skills-content"),
  skillDirBadge:    $("skill-dir-badge"),
  btnRefreshSkills: $("btn-refresh-skills"),
  // settings modal
  wizardOverlay:    $("wizard-overlay"),
  wizardBody:       $("wizard-body"),
  settingsTabs:     $("settings-tabs"),
  wbtnClose:        $("wbtn-close"),
  wbtnSave:         $("wbtn-save"),
  wstatus:          $("wstatus"),
};

// ── Connectors state ───────────────────────────────────────────────────────

const connectors = {
  telegram: {
    enabled: false, bot_token: "", bot_token_set: false,
    agent: "default", allowlist: "", group_allowlist: "",
    group_policy: "allowlist", require_mention: false, stream: true,
  },
  feishu: {
    enabled: false, app_id: "",
    app_secret: "", app_secret_set: false,
    encrypt_key: "", encrypt_key_set: false,
    verification_token: "", verification_token_set: false,
    agent: "default", allowlist: "", stream: false,
  },
  discord: {
    enabled: false, bot_token: "", bot_token_set: false,
    agent: "default", allowlist: "", guild_allowlist: "",
    require_mention: true, stream: true,
  },
  slack: {
    enabled: false, bot_token: "", bot_token_set: false,
    app_token: "", app_token_set: false,
    agent: "default", allowlist: "", stream: true,
  },
  dingtalk: {
    enabled: false, client_id: "",
    client_secret: "", client_secret_set: false,
    agent: "default", allowlist: "", stream: true,
  },
  wecom: {
    enabled: false, corp_id: "",
    corp_secret: "", corp_secret_set: false,
    agent_id: 0, token: "", token_set: false,
    agent: "default", allowlist: "",
  },
};

// ── Browser state ──────────────────────────────────────────────────────────

const browser = {
  enabled: true,
  headless: true,
  timeout: 30,
  playwright_installed: false,
};

// ── Email & Calendar state ─────────────────────────────────────────────────

const emailCfg = {
  enabled: false, imap_host: "", imap_port: 993,
  smtp_host: "", smtp_port: 587, username: "",
  password: "", password_set: false, mailbox: "INBOX",
};

const calendarCfg = {
  enabled: false, url: "", username: "",
  password: "", password_set: false, calendar_name: "",
};

// ── Skills state ───────────────────────────────────────────────────────────

const skills = {
  installed: [],
  skillDir: "",
  configured: false,
  repos: [],
  reposLoading: false,
  reposError: "",
  installing: new Set(),  // URLs currently being installed
};

// ── WebSocket ──────────────────────────────────────────────────────────────

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const host = location.host || "127.0.0.1:8765";
  const params = new URLSearchParams(location.search);
  const key = params.get("api_key") || "";
  const q = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${proto}//${host}${q}`;
}

function connect() {
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  setConnStatus("reconnecting");
  let ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (err) {
    setConnStatus("disconnected");
    insertErrorMsg(`WebSocket init failed: ${String(err)}`);
    scheduleReconnect();
    return;
  }
  state.ws = ws;

  ws.onopen = () => {
    setConnStatus("connected");
    state._reconnectDelay = 1000;
    els.btnSend.disabled = false;
    document.getElementById("msg-connecting")?.remove();
    send({ type: "list_agents" });
    send({ type: "list_sessions" });
    // config_status is pushed automatically by server on connect

    // Reset any UI state that was waiting for a response on the old connection.
    // (config_saved / test_provider_result are lost when the WS drops.)
    clearTimeout(_wizardSaveTimer); _wizardSaveTimer = null;
    clearTimeout(_testTimer);       _testTimer = null;
    if (wizard.open && wizard.saving) {
      wizard.saving = false;
      els.wbtnSave.disabled = false;
      els.wbtnSave.textContent = "💾 Save";
      els.wstatus.textContent = "✗ Connection lost — please try again.";
      els.wstatus.className = "wstatus err";
    }
    const testBtn = document.getElementById("wiz-test-btn");
    if (testBtn && testBtn.disabled) {
      testBtn.disabled = false;
      testBtn.textContent = "Test Connection";
      const res = document.getElementById("wiz-test-result");
      if (res) { res.style.color = "var(--err)"; res.textContent = "✗ Connection lost — please retry."; }
    }
  };

  ws.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    handleMessage(data);
  };

  ws.onclose = (ev) => {
    setConnStatus("disconnected");
    els.btnSend.disabled = true;
    const reason = ev && ev.reason ? ` (${ev.reason})` : "";
    insertSystemMsg(`Disconnected: code ${ev.code}${reason}`);
    scheduleReconnect();
  };

  ws.onerror = () => {
    insertErrorMsg(`WebSocket error to ${wsUrl()}`);
    ws.close();
  };
}

function scheduleReconnect() {
  if (state._reconnectTimer) return;
  const delay = state._reconnectDelay;
  state._reconnectDelay = Math.min(delay * 2, 30000);
  state._reconnectTimer = setTimeout(() => {
    state._reconnectTimer = null;
    connect();
  }, delay);
}

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

// ── Message dispatcher ────────────────────────────────────────────────────

function handleMessage(data) {
  switch (data.type) {
    case "config_status":
      handleConfigStatus(data);
      break;
    case "config_saved":
      handleConfigSaved(data);
      break;
    case "session":
      state.session_id = data.session_id;
      els.sessionLabel.textContent = `session: ${data.session_id}`;
      break;
    case "chunk":
      if (data.text) appendChunk(data.text);
      break;
    case "tool_call":
      insertToolBubble(data);
      break;
    case "tool_result":
      updateToolBubble(data);
      if (data.tool === "browser_open_for_user" && !data.is_error) {
        els.handoverBanner.classList.remove("hidden");
        els.handoverMsg.textContent =
          "🔐 Browser window opened — complete your action, then click Done";
      }
      if (data.tool === "browser_wait_for_user") {
        els.handoverBanner.classList.add("hidden");
      }
      break;
    case "stopped":
      finalizeAiMsg();
      setSending(false);
      break;
    case "session_history":
      renderSessionHistory(data.session_id, data.turns || []);
      break;
    case "compaction":
      insertSystemMsg(`Context compacted — archived ${data.archived} turns, kept ${data.kept}.`);
      break;
    case "done":
      finalizeAiMsg();
      state.inTokens  += data.input_tokens  || 0;
      state.outTokens += data.output_tokens || 0;
      updateTokenStats();
      setSending(false);
      send({ type: "list_agents" });
      send({ type: "list_sessions" });
      break;
    case "error":
      finalizeAiMsg();
      insertErrorMsg(data.message || "Unknown error");
      setSending(false);
      break;
    case "agents":
      populateAgents(data.items || []);
      break;
    case "sessions":
      renderSessions(data.items || []);
      break;
    case "memories":
      renderMemories(data.items || []);
      break;
    case "memory_deleted":
      onMemoryDeleted(data.note_id, data.ok);
      break;
    case "pipeline_step":
      insertSystemMsg(`Pipeline step [${data.agent}]: ${data.output || ""}`);
      break;
    case "pong":
      break;
    case "models":
      handleModelsResponse(data);
      break;
    case "skills":
      handleSkillsList(data);
      break;
    case "skill_repos":
      handleSkillRepos(data);
      break;
    case "skill_install_progress":
      showSkillToast(data.message || "Installing…", "info");
      break;
    case "skill_install_result":
      handleSkillInstallResult(data);
      break;
    case "publish_skill_url":
      handlePublishSkillUrl(data);
      break;
    case "test_provider_result":
      handleTestProviderResult(data);
      break;
    case "todos":
      renderTodos(data.items || []);
      break;
    case "todo_created":
      onTodoCreated(data.item);
      break;
    case "todo_updated":
      onTodoUpdated(data.item);
      break;
    case "todo_deleted":
      onTodoDeleted(data.todo_id, data.ok);
      break;
    case "scheduled_tasks":
      renderScheduledTasks(data.tasks || []);
      break;
    case "task_created":
      onTaskCreated(data.task);
      break;
    case "task_toggled":
      onTaskToggled(data.task_id, data.enabled, data.ok);
      break;
    case "task_triggered":
      break;
    case "task_cancelled":
      if (data.ok) {
        send({ type: "list_scheduled_tasks" });
      }
      break;
  }
}

function handleTestProviderResult(data) {
  clearTimeout(_testTimer);
  _testTimer = null;
  const testBtn    = document.getElementById("wiz-test-btn");
  const testResult = document.getElementById("wiz-test-result");
  if (testBtn) { testBtn.disabled = false; testBtn.textContent = "Test Connection"; }
  if (!testResult) return;
  if (data.ok) {
    testResult.style.color = "var(--ok)";
    testResult.textContent = "✓ " + (data.detail || "Connection successful.");
  } else {
    testResult.style.color = "var(--err)";
    testResult.textContent = "✗ " + (data.detail || "Connection failed.");
  }
}

// ── Settings modal ─────────────────────────────────────────────────────────

function handleConfigStatus(cfg) {
  wizard.serverConfig = cfg;

  // Update wizard fields when closed, OR when just opened from settings button.
  if (!wizard.open || wizard._pendingRefresh) {
    wizard._pendingRefresh = false;
    const prov = providerById(cfg.provider);
    wizard.provider      = prov.id;
    wizard.model         = cfg.model || prov.defaultModel;
    wizard.baseUrl       = cfg.base_url || prov.defaultBaseUrl || "";
    wizard.apiKey        = "";
    wizard.maxTokens     = cfg.max_tokens     || 4096;
    wizard.maxToolRounds = cfg.max_tool_rounds || 10;
    wizard.systemPrompt  = cfg.system_prompt  || "";
    wizard.costIn        = cfg.cost_per_1k_input_tokens  || 0.0;
    wizard.costOut       = cfg.cost_per_1k_output_tokens || 0.0;
    // Memory / context fields
    const ctx = cfg.context || {};
    wizard.historyBudget        = ctx.history_budget        ?? 60000;
    wizard.compactThreshold     = ctx.compact_threshold     ?? 0.85;
    wizard.compactKeepTurns     = ctx.compact_keep_turns    ?? 6;
    wizard.compactStrategy      = ctx.compact_strategy      || "lossless";
    wizard.memoryMinScore       = ctx.memory_min_score      ?? 0.25;
    wizard.memoryMaxTokens      = ctx.memory_max_tokens     ?? 800;
    wizard.autoExtract          = ctx.auto_extract          ?? true;
    wizard.memoryDecayRate      = ctx.memory_decay_rate     ?? 0.0;
    wizard.retrievalTemperature = ctx.retrieval_temperature ?? 0.0;
    wizard.serendipityBudget    = ctx.serendipity_budget    ?? 0.0;
    // Re-render the modal with fresh data if it's already open
    if (wizard.open) renderSettingsModal();
  }

  // Always populate connectors state (used in channels tab)
  if (cfg.connectors) {
    const tg = cfg.connectors.telegram || {};
    connectors.telegram.enabled         = Boolean(tg.enabled);
    connectors.telegram.bot_token       = "";
    connectors.telegram.bot_token_set   = Boolean(tg.bot_token_set);
    connectors.telegram.agent           = tg.agent || "default";
    connectors.telegram.allowlist       = (tg.allowlist || []).join(", ");
    connectors.telegram.group_allowlist = (tg.group_allowlist || []).join(", ");
    connectors.telegram.group_policy    = tg.group_policy || "allowlist";
    connectors.telegram.require_mention = Boolean(tg.require_mention);
    connectors.telegram.stream          = tg.stream !== false;

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
    connectors.feishu.allowlist              = (fs.allowlist || []).join(", ");
    connectors.feishu.stream                 = Boolean(fs.stream);

    const dc = cfg.connectors.discord || {};
    connectors.discord.enabled          = Boolean(dc.enabled);
    connectors.discord.bot_token        = "";
    connectors.discord.bot_token_set    = Boolean(dc.bot_token_set);
    connectors.discord.agent            = dc.agent || "default";
    connectors.discord.allowlist        = (dc.allowlist || []).join(", ");
    connectors.discord.guild_allowlist  = (dc.guild_allowlist || []).join(", ");
    connectors.discord.require_mention  = dc.require_mention !== false;
    connectors.discord.stream           = dc.stream !== false;

    const sl = cfg.connectors.slack || {};
    connectors.slack.enabled            = Boolean(sl.enabled);
    connectors.slack.bot_token          = "";
    connectors.slack.bot_token_set      = Boolean(sl.bot_token_set);
    connectors.slack.app_token          = "";
    connectors.slack.app_token_set      = Boolean(sl.app_token_set);
    connectors.slack.agent              = sl.agent || "default";
    connectors.slack.allowlist          = (sl.allowlist || []).join(", ");
    connectors.slack.stream             = sl.stream !== false;

    const dt = cfg.connectors.dingtalk || {};
    connectors.dingtalk.enabled           = Boolean(dt.enabled);
    connectors.dingtalk.client_id         = dt.client_id || "";
    connectors.dingtalk.client_secret     = "";
    connectors.dingtalk.client_secret_set = Boolean(dt.client_secret_set);
    connectors.dingtalk.agent             = dt.agent || "default";
    connectors.dingtalk.allowlist         = (dt.allowlist || []).join(", ");
    connectors.dingtalk.stream            = dt.stream !== false;

    const wc = cfg.connectors.wecom || {};
    connectors.wecom.enabled            = Boolean(wc.enabled);
    connectors.wecom.corp_id            = wc.corp_id || "";
    connectors.wecom.corp_secret        = "";
    connectors.wecom.corp_secret_set    = Boolean(wc.corp_secret_set);
    connectors.wecom.agent_id           = wc.agent_id || 0;
    connectors.wecom.token              = "";
    connectors.wecom.token_set          = Boolean(wc.token_set);
    connectors.wecom.agent              = wc.agent || "default";
    connectors.wecom.allowlist          = (wc.allowlist || []).join(", ");
  }

  if (cfg.browser) {
    browser.enabled              = cfg.browser.enabled ?? true;
    browser.headless             = cfg.browser.headless ?? true;
    browser.timeout              = cfg.browser.timeout ?? 30;
    browser.playwright_installed = cfg.browser.playwright_installed ?? false;
  }

  if (cfg.email) {
    emailCfg.enabled      = Boolean(cfg.email.enabled);
    emailCfg.imap_host    = cfg.email.imap_host    || "";
    emailCfg.imap_port    = cfg.email.imap_port    || 993;
    emailCfg.smtp_host    = cfg.email.smtp_host    || "";
    emailCfg.smtp_port    = cfg.email.smtp_port    || 587;
    emailCfg.username     = cfg.email.username     || "";
    emailCfg.password_set = Boolean(cfg.email.password_set);
    emailCfg.mailbox      = cfg.email.mailbox      || "INBOX";
  }
  if (cfg.calendar) {
    calendarCfg.enabled       = Boolean(cfg.calendar.enabled);
    calendarCfg.url           = cfg.calendar.url           || "";
    calendarCfg.username      = cfg.calendar.username      || "";
    calendarCfg.password_set  = Boolean(cfg.calendar.password_set);
    calendarCfg.calendar_name = cfg.calendar.calendar_name || "";
  }

  if (!cfg.configured && !wizard.open) {
    openWizard(false /* not dismissible until saved */);
  }
}

function handleConfigSaved(data) {
  clearTimeout(_wizardSaveTimer);
  _wizardSaveTimer = null;
  wizard.saving = false;
  els.wbtnSave.disabled = false;
  els.wbtnSave.textContent = "💾 Save";

  if (data.ok) {
    wizard.savedOnce = true;
    els.wbtnClose.style.display = "";   // show Close even if originally non-dismissible
    els.wstatus.textContent = "✓ Saved";
    els.wstatus.className = "wstatus ok";
    // Drop current session so the next chat uses the new provider config
    state.session_id = null;
    els.sessionLabel.textContent = "";
    // Auto-clear status after 3 s, then refresh credentials from server
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

function openWizard(dismissible = true) {
  wizard.open        = true;
  wizard.dismissible = dismissible;
  els.wizardOverlay.classList.remove("hidden");
  els.wbtnClose.style.display = (dismissible || wizard.savedOnce) ? "" : "none";
  renderSettingsModal();
}

function closeWizard() {
  wizard.open = false;
  els.wizardOverlay.classList.add("hidden");
}

// ── Settings tab rendering ─────────────────────────────────────────────────

function renderSettingsTabs() {
  const tabs = [
    { id: "model",        label: "🤖 Model" },
    { id: "channels",     label: "📡 Channels" },
    { id: "system",       label: "⚙ System" },
    { id: "memory",       label: "🧠 Memory" },
    { id: "integrations", label: "📧 Email & Calendar" },
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

function renderSettingsModal() {
  renderSettingsTabs();
  switch (wizard.tab) {
    case "model":        renderModelTab();        break;
    case "channels":     renderChannelsTab();     break;
    case "system":       renderSystemTab();       break;
    case "memory":       renderMemoryTab();       break;
    case "integrations": renderIntegrationsTab(); break;
  }
}

// ── Model tab ──────────────────────────────────────────────────────────────

function renderModelTab() {
  const prov = providerById(wizard.provider);
  const sc   = wizard.serverConfig;

  // Provider cards
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

  // API Key + Base URL
  let keyHtml = `<div class="settings-section"><h3 class="settings-section-h">API Key &amp; Endpoint</h3>`;
  if (prov.needsKey) {
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
    keyHtml += `
      <div class="wfield">
        <label>${escHtml(prov.baseUrlLabel)}</label>
        <input type="text" id="wiz-baseurl" placeholder="${escHtml(prov.defaultBaseUrl)}"
               value="${escHtml(burl)}">
        <div class="wfield-hint">Leave as-is unless you're using a proxy or custom endpoint.</div>
      </div>`;
  }
  keyHtml += `
    <div style="margin-top:12px;display:flex;align-items:center;gap:12px">
      <button type="button" id="wiz-test-btn" class="secondary" style="flex-shrink:0">Test Connection</button>
      <span id="wiz-test-result" style="font-size:13px"></span>
    </div>
  </div>`;

  // Model
  const suggestions  = prov.modelSuggestions;
  const currentModel = wizard.model || prov.defaultModel;
  const listId       = "wiz-model-list";
  const optionsHtml  = suggestions.map((m) => `<option value="${escHtml(m)}">`).join("");
  const modelHtml = `
    <div class="settings-section">
      <h3 class="settings-section-h">Model</h3>
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

  els.wizardBody.innerHTML = cardsHtml + keyHtml + modelHtml;

  // Wire provider radios
  els.wizardBody.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      wizard.provider = radio.value;
      const p2 = providerById(wizard.provider);
      wizard.model   = p2.defaultModel;
      wizard.baseUrl = p2.defaultBaseUrl || "";
      renderModelTab();
    });
  });
  els.wizardBody.querySelectorAll(".provider-card").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector("input[type=radio]");
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
    });
  });

  // Wire API key / base URL
  const keyEl  = document.getElementById("wiz-apikey");
  const burlEl = document.getElementById("wiz-baseurl");
  if (keyEl)  keyEl.addEventListener("input",  () => { wizard.apiKey  = keyEl.value.trim(); });
  if (burlEl) burlEl.addEventListener("input", () => { wizard.baseUrl = burlEl.value.trim(); });

  // Test Connection button
  const testBtn    = document.getElementById("wiz-test-btn");
  const testResult = document.getElementById("wiz-test-result");
  if (testBtn) {
    testBtn.addEventListener("click", () => {
      clearTimeout(_testTimer);
      testBtn.disabled = true;
      testBtn.textContent = "⠸ Testing…";
      testResult.textContent = "";
      testResult.style.color = "var(--muted)";
      _testTimer = setTimeout(() => {
        _testTimer = null;
        const btn = document.getElementById("wiz-test-btn");
        const res = document.getElementById("wiz-test-result");
        if (btn) { btn.disabled = false; btn.textContent = "Test Connection"; }
        if (res) { res.style.color = "var(--err)"; res.textContent = "✗ Timed out (30 s). Check your API key and endpoint."; }
      }, 30000);
      send({ type: "test_provider", provider: wizard.provider, api_key: wizard.apiKey, base_url: wizard.baseUrl, model: wizard.model });
    });
  }

  // Wire model selection
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

  // Request model list from server
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: "list_models", provider: wizard.provider,
      api_key: wizard.apiKey, base_url: wizard.baseUrl || prov.defaultBaseUrl,
    }));
  } else {
    document.getElementById("wiz-model-loading")?.remove();
  }
}

function handleModelsResponse(msg) {
  if (!wizard.open || wizard.tab !== "model") return;
  const loadingEl = document.getElementById("wiz-model-loading");
  const selectEl  = document.getElementById("wiz-model-select");
  const inputEl   = document.getElementById("wiz-model");

  if (loadingEl) loadingEl.remove();

  if (msg.items && msg.items.length > 0) {
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

// ── Channels tab ───────────────────────────────────────────────────────────

// Channel definitions — order determines display order
const CHANNELS = [
  {
    id: "telegram",
    icon: "✈",
    name: "Telegram Bot",
    desc: "Long-polling bot. Zero extra deps. Supports streaming replies.",
    setupUrl: "https://t.me/BotFather",
    setupLabel: "@BotFather",
    fields: (c) => `
      <div class="wfield">
        <label>Bot Token</label>
        <input type="password" id="tg-token" autocomplete="off"
               placeholder="123456:ABCDEF…" value="${escHtml(c.bot_token)}">
        <div class="wfield-hint">${_credHint(c.bot_token_set)}
          Get one from <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>.
        </div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="tg-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>DM Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="tg-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="123456789, 987654321">
        <div class="wfield-hint">Comma-separated user IDs for direct messages. Empty = allow everyone.</div>
      </div>
      <div class="wfield">
        <label>Group Policy</label>
        <select id="tg-group-policy">
          ${["open","allowlist","disabled"].map((v) =>
            `<option value="${v}"${c.group_policy===v?" selected":""}>${v}</option>`
          ).join("")}
        </select>
        <div class="wfield-hint">
          <b>open</b> — respond to any group message.
          <b>allowlist</b> — only groups in the list below.
          <b>disabled</b> — ignore all group messages.
        </div>
      </div>
      <div class="wfield">
        <label>Group Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="tg-group-allowlist" value="${escHtml(c.group_allowlist)}"
               placeholder="-100123456789, -100987654321">
        <div class="wfield-hint">Comma-separated group/supergroup chat IDs (negative numbers).</div>
      </div>
      <div class="wfield wfield-row">
        <label>Require @mention in groups</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="tg-require-mention" ${c.require_mention ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Only respond when the bot is @mentioned in group chats.</div>
      </div>
      <div class="wfield wfield-row">
        <label>Streaming replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="tg-stream" ${c.stream ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Edit message progressively as text arrives (simulates streaming).</div>
      </div>`,
  },
  {
    id: "feishu",
    icon: "🪁",
    name: "Feishu / Lark",
    desc: "WebSocket long-connection bot. Requires app_id and app_secret.",
    setupUrl: "https://open.feishu.cn/app",
    setupLabel: "Feishu Open Platform",
    fields: (c) => `
      <div class="wfield">
        <label>App ID</label>
        <input type="text" id="fs-appid" autocomplete="off"
               placeholder="cli_xxxxxxxxxx" value="${escHtml(c.app_id)}">
        <div class="wfield-hint">Found in Feishu Open Platform → App credentials.</div>
      </div>
      <div class="wfield">
        <label>App Secret</label>
        <input type="password" id="fs-secret" autocomplete="off"
               placeholder="App Secret" value="${escHtml(c.app_secret)}">
        <div class="wfield-hint">${_credHint(c.app_secret_set)}</div>
      </div>
      <div class="wfield">
        <label>Encrypt Key <span class="wfield-optional">(optional)</span></label>
        <input type="password" id="fs-encrypt-key" autocomplete="off"
               placeholder="Encrypt Key" value="${escHtml(c.encrypt_key)}">
        <div class="wfield-hint">${_credHint(c.encrypt_key_set)}
          Required only if message encryption is enabled in Feishu Open Platform → Event subscriptions.
        </div>
      </div>
      <div class="wfield">
        <label>Verification Token <span class="wfield-optional">(optional)</span></label>
        <input type="password" id="fs-verify-token" autocomplete="off"
               placeholder="Verification Token" value="${escHtml(c.verification_token)}">
        <div class="wfield-hint">${_credHint(c.verification_token_set)}
          Required only if verification token is enabled in Feishu Open Platform → Event subscriptions.
        </div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="fs-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>Chat Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="fs-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="oc_xxxxxxxx, oc_yyyyyyyy">
        <div class="wfield-hint">Comma-separated Feishu chat IDs. Empty = allow all.</div>
      </div>
      <div class="wfield wfield-row">
        <label>Streaming replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="fs-stream" ${c.stream ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Requires Interactive Card permissions in Feishu Open Platform.</div>
      </div>`,
  },
  {
    id: "discord",
    icon: "🎮",
    name: "Discord Bot",
    desc: "WebSocket gateway bot. Responds to DMs and @mentions in servers.",
    setupUrl: "https://discord.com/developers/applications",
    setupLabel: "Discord Developer Portal",
    fields: (c) => `
      <div class="wfield">
        <label>Bot Token</label>
        <input type="password" id="dc-token" autocomplete="off"
               placeholder="MTxxxxxxxx.xxxxxx.xxxxxxxxxxxx" value="${escHtml(c.bot_token)}">
        <div class="wfield-hint">${_credHint(c.bot_token_set)}
          <a href="https://discord.com/developers/applications" target="_blank" rel="noopener">Developer Portal</a>
          → Your App → Bot → Token. Enable Message Content Intent.
        </div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="dc-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>User Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="dc-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="123456789012345678, …">
        <div class="wfield-hint">Comma-separated Discord user IDs (18-digit snowflakes). Empty = allow all.</div>
      </div>
      <div class="wfield">
        <label>Server Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="dc-guild-allowlist" value="${escHtml(c.guild_allowlist)}"
               placeholder="987654321098765432, …">
        <div class="wfield-hint">Comma-separated server (guild) IDs. Empty = all servers.</div>
      </div>
      <div class="wfield wfield-row">
        <label>Require @mention in servers</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="dc-require-mention" ${c.require_mention ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Only respond when @mentioned in server channels (DMs always respond).</div>
      </div>
      <div class="wfield wfield-row">
        <label>Streaming replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="dc-stream" ${c.stream ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Edit the message progressively as text arrives.</div>
      </div>`,
  },
  {
    id: "slack",
    icon: "🔧",
    name: "Slack",
    desc: "Socket Mode WebSocket bot. No public HTTP endpoint required.",
    setupUrl: "https://api.slack.com/apps",
    setupLabel: "Slack API Console",
    fields: (c) => `
      <div class="wfield">
        <label>Bot Token <span class="wfield-optional">(xoxb-…)</span></label>
        <input type="password" id="sl-bot-token" autocomplete="off"
               placeholder="xoxb-…" value="${escHtml(c.bot_token)}">
        <div class="wfield-hint">${_credHint(c.bot_token_set)}
          OAuth &amp; Permissions → Bot User OAuth Token.
        </div>
      </div>
      <div class="wfield">
        <label>App Token <span class="wfield-optional">(xapp-…)</span></label>
        <input type="password" id="sl-app-token" autocomplete="off"
               placeholder="xapp-…" value="${escHtml(c.app_token)}">
        <div class="wfield-hint">${_credHint(c.app_token_set)}
          App-Level Tokens → Create token with <code>connections:write</code> scope. Enable Socket Mode.
        </div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="sl-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>Channel Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="sl-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="C04XXXXXXX, D04YYYYYYY">
        <div class="wfield-hint">Comma-separated channel IDs (C… public, D… DMs). Empty = all channels.</div>
      </div>
      <div class="wfield wfield-row">
        <label>Streaming replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="sl-stream" ${c.stream ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Update the message progressively as text arrives.</div>
      </div>`,
  },
  {
    id: "dingtalk",
    icon: "🔔",
    name: "DingTalk 钉钉",
    desc: "Stream mode WebSocket bot. No public endpoint needed. 钉钉企业内部应用。",
    setupUrl: "https://open.dingtalk.com/developer",
    setupLabel: "DingTalk Open Platform",
    fields: (c) => `
      <div class="wfield">
        <label>Client ID (App Key)</label>
        <input type="text" id="dt-client-id" autocomplete="off"
               placeholder="dingxxxxxxxxxxxx" value="${escHtml(c.client_id)}">
        <div class="wfield-hint">DingTalk Open Platform → App → Credentials &amp; Basic Info → AppKey.
          Enable Stream Push Mode under Subscription Management.</div>
      </div>
      <div class="wfield">
        <label>Client Secret (App Secret)</label>
        <input type="password" id="dt-client-secret" autocomplete="off"
               placeholder="App Secret" value="${escHtml(c.client_secret)}">
        <div class="wfield-hint">${_credHint(c.client_secret_set)}</div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="dt-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>User Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="dt-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="user_openid1, user_openid2">
        <div class="wfield-hint">Comma-separated DingTalk user open IDs. Empty = allow everyone.</div>
      </div>`,
  },
  {
    id: "wecom",
    icon: "💬",
    name: "WeCom 企业微信",
    desc: "HTTP callback webhook. Requires a publicly accessible server URL. 企业微信企业内部应用。",
    setupUrl: "https://work.weixin.qq.com/wework_admin/frame#apps",
    setupLabel: "WeCom Admin Console",
    fields: (c) => `
      <div class="wfield">
        <label>Corp ID</label>
        <input type="text" id="wc-corp-id" autocomplete="off"
               placeholder="ww…" value="${escHtml(c.corp_id)}">
        <div class="wfield-hint">WeCom Admin → My Enterprise → Enterprise Info → Enterprise ID.</div>
      </div>
      <div class="wfield">
        <label>Corp Secret</label>
        <input type="password" id="wc-corp-secret" autocomplete="off"
               placeholder="App Secret" value="${escHtml(c.corp_secret)}">
        <div class="wfield-hint">${_credHint(c.corp_secret_set)}
          WeCom Admin → App Management → Your App → API → Secret.
        </div>
      </div>
      <div class="wfield">
        <label>Agent ID</label>
        <input type="number" id="wc-agent-id" value="${c.agent_id || 0}" min="0">
        <div class="wfield-hint">App AgentID from WeCom Admin → App Management.</div>
      </div>
      <div class="wfield">
        <label>Callback Token</label>
        <input type="password" id="wc-token" autocomplete="off"
               placeholder="Your callback token" value="${escHtml(c.token)}">
        <div class="wfield-hint">${_credHint(c.token_set)}
          Set in WeCom Admin → App → Receive Messages → Set Token.
          Webhook URL: <code>http(s)://your-server/webhook/wecom</code>
        </div>
      </div>
      <div class="wfield">
        <label>Agent</label>
        <input type="text" id="wc-agent" value="${escHtml(c.agent)}" placeholder="default">
      </div>
      <div class="wfield">
        <label>User Allowlist <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="wc-allowlist" value="${escHtml(c.allowlist)}"
               placeholder="zhangsan, lisi">
        <div class="wfield-hint">Comma-separated WeCom user IDs. Empty = allow everyone.</div>
      </div>`,
  },
];

function _credHint(isSet) {
  return isSet
    ? '<span class="conn-set-badge">SET</span> Leave blank to keep current value.'
    : "";
}

function renderChannelsTab() {
  els.wizardBody.innerHTML = `<div class="conn-panel">` +
    CHANNELS.map((ch) => {
      const c   = connectors[ch.id];
      const on  = c.enabled;
      return `
        <div class="conn-section" id="conn-${ch.id}">
          <div class="conn-section-header">
            <span class="conn-platform-icon">${ch.icon}</span>
            <div class="conn-platform-info">
              <span class="conn-platform-name">${ch.name}</span>
              <span class="conn-platform-desc">${ch.desc}</span>
            </div>
            <label class="toggle-switch" title="${on ? "Enabled" : "Disabled"}">
              <input type="checkbox" id="${ch.id}-enabled" ${on ? "checked" : ""}
                     data-chan="${ch.id}">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="conn-fields" id="${ch.id}-fields" style="${on ? "" : "display:none"}">
            ${ch.fields(c)}
            <div class="wfield-hint" style="margin-top:4px">
              Setup guide: <a href="${ch.setupUrl}" target="_blank" rel="noopener">${ch.setupLabel} ↗</a>
            </div>
          </div>
        </div>`;
    }).join("") +
    `</div>`;

  // Wire up enable toggles
  CHANNELS.forEach(({ id }) => {
    document.getElementById(`${id}-enabled`).addEventListener("change", (e) => {
      document.getElementById(`${id}-fields`).style.display = e.target.checked ? "" : "none";
    });
  });
}

// ── System tab ─────────────────────────────────────────────────────────────

function renderSystemTab() {
  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Generation</h3>
      <div class="wfield">
        <label>Max output tokens</label>
        <input type="number" id="sys-max-tokens" min="256" max="32768" step="256"
               value="${escHtml(String(wizard.maxTokens))}">
        <div class="wfield-hint">Maximum tokens the model generates per response.</div>
      </div>
      <div class="wfield">
        <label>Max tool rounds</label>
        <input type="number" id="sys-max-tool-rounds" min="1" max="100" step="1"
               value="${escHtml(String(wizard.maxToolRounds))}">
        <div class="wfield-hint">Maximum tool calls per agent turn before forcing a final response.</div>
      </div>
      <div class="wfield">
        <label>System prompt</label>
        <textarea id="sys-system-prompt" rows="5"
                  style="width:100%;box-sizing:border-box;resize:vertical"
                  placeholder="You are HushClaw, a helpful AI assistant…">${escHtml(wizard.systemPrompt)}</textarea>
        <div class="wfield-hint">Base persona for the agent. Leave blank to keep the current prompt.</div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Pricing <span class="wfield-optional">(optional)</span></h3>
      <p class="wdesc">Used for cost estimation in the chat UI. Set to 0.0 to disable.</p>
      <div class="wfield">
        <label>Input cost (USD / 1k tokens)</label>
        <input type="number" id="sys-cost-in" min="0" step="0.0001"
               value="${escHtml(String(wizard.costIn))}">
      </div>
      <div class="wfield">
        <label>Output cost (USD / 1k tokens)</label>
        <input type="number" id="sys-cost-out" min="0" step="0.0001"
               value="${escHtml(String(wizard.costOut))}">
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">API Rate Limits</h3>
      <p class="wdesc">
        HushClaw does not control provider-side rate limits or credit quotas.
        If you see errors like "Key limit exceeded" (e.g., on OpenRouter), manage your
        limits directly on your provider's dashboard.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent-h)">
          OpenRouter Key Settings ↗
        </a>
        <a href="https://platform.openai.com/usage" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent-h)">
          OpenAI Usage ↗
        </a>
        <a href="https://console.anthropic.com" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent-h)">
          Anthropic Console ↗
        </a>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Browser</h3>
      <p class="wdesc">
        Enables JS-rendered page fetching, clicking, form filling, and screenshots.
        Playwright (Chromium) is installed automatically on first use.
      </p>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Enable browser tools</span>
          <span class="connector-badge ${browser.playwright_installed ? 'badge-set' : ''}">
            ${browser.playwright_installed ? 'playwright installed' : 'auto-install on first use'}
          </span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="br-enabled" ${browser.enabled ? 'checked' : ''}
                 onchange="document.getElementById('br-fields').style.display=this.checked?'':'none'">
          <span class="slider"></span>
        </label>
      </div>
      <div id="br-fields" style="${browser.enabled ? '' : 'display:none'}">
        <div class="connector-row">
          <div class="connector-meta">
            <span class="connector-name">Headless mode</span>
            <span class="connector-desc">Hide browser window (disable for debugging)</span>
          </div>
          <label class="toggle">
            <input type="checkbox" id="br-headless" ${browser.headless ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="wfield" style="margin-top:8px">
          <label>Operation timeout (seconds)</label>
          <input type="number" id="br-timeout" min="5" max="120" step="5"
                 value="${browser.timeout}">
        </div>
      </div>
    </div>
  `;
}

// ── Memory tab ─────────────────────────────────────────────────────────────

function renderMemoryTab() {
  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Context &amp; Compaction</h3>
      <p class="wdesc">Controls how much conversation history is kept in context and when old turns are archived.</p>
      <div class="wfield">
        <label>History budget (tokens)</label>
        <input type="number" id="mem-history-budget" min="1000" max="200000" step="1000"
               value="${escHtml(String(wizard.historyBudget))}">
        <div class="wfield-hint">Maximum tokens of conversation history kept in context before compaction triggers.</div>
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
        <input type="number" id="mem-max-tokens" min="100" max="8000" step="100"
               value="${escHtml(String(wizard.memoryMaxTokens))}">
        <div class="wfield-hint">Hard cap on tokens spent on injected memories per request.</div>
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
}

// ── Integrations tab (Email & Calendar) ─────────────────────────────────────

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

function renderIntegrationsTab() {
  const pwdPlaceholder = emailCfg.password_set ? "••••••••  (already set)" : "App password";
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

  // Email preset quick-fill buttons
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

  // CalDAV preset quick-fill buttons
  document.querySelectorAll("[data-cal-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = CALDAV_PROVIDERS[parseInt(btn.dataset.calPreset)];
      if (!p) return;
      document.getElementById("calendar-url").value = p.url;
    });
  });
}

// ── Settings save ──────────────────────────────────────────────────────────

function syncFormToState() {
  // Model tab
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

  // Channels tab — sync all 6 platforms
  function _fv(id) { const el = document.getElementById(id); return el ? el.value.trim() : ""; }
  function _fc(id, fallback) { const el = document.getElementById(id); return el ? el.checked : fallback; }

  if (document.getElementById("telegram-enabled")) {
    const c = connectors.telegram;
    c.enabled         = _fc("telegram-enabled", c.enabled);
    c.bot_token       = _fv("tg-token");
    c.agent           = _fv("tg-agent") || "default";
    c.allowlist       = _fv("tg-allowlist");
    c.group_allowlist = _fv("tg-group-allowlist");
    c.group_policy    = _fv("tg-group-policy") || "allowlist";
    c.require_mention = _fc("tg-require-mention", c.require_mention);
    c.stream          = _fc("tg-stream", c.stream);
  }
  if (document.getElementById("feishu-enabled")) {
    const c = connectors.feishu;
    c.enabled             = _fc("feishu-enabled", c.enabled);
    c.app_id              = _fv("fs-appid");
    c.app_secret          = _fv("fs-secret");
    c.encrypt_key         = _fv("fs-encrypt-key");
    c.verification_token  = _fv("fs-verify-token");
    c.agent               = _fv("fs-agent") || "default";
    c.allowlist           = _fv("fs-allowlist");
    c.stream              = _fc("fs-stream", c.stream);
  }
  if (document.getElementById("discord-enabled")) {
    const c = connectors.discord;
    c.enabled         = _fc("discord-enabled", c.enabled);
    c.bot_token       = _fv("dc-token");
    c.agent           = _fv("dc-agent") || "default";
    c.allowlist       = _fv("dc-allowlist");
    c.guild_allowlist = _fv("dc-guild-allowlist");
    c.require_mention = _fc("dc-require-mention", c.require_mention);
    c.stream          = _fc("dc-stream", c.stream);
  }
  if (document.getElementById("slack-enabled")) {
    const c = connectors.slack;
    c.enabled    = _fc("slack-enabled", c.enabled);
    c.bot_token  = _fv("sl-bot-token");
    c.app_token  = _fv("sl-app-token");
    c.agent      = _fv("sl-agent") || "default";
    c.allowlist  = _fv("sl-allowlist");
    c.stream     = _fc("sl-stream", c.stream);
  }
  if (document.getElementById("dingtalk-enabled")) {
    const c = connectors.dingtalk;
    c.enabled       = _fc("dingtalk-enabled", c.enabled);
    c.client_id     = _fv("dt-client-id");
    c.client_secret = _fv("dt-client-secret");
    c.agent         = _fv("dt-agent") || "default";
    c.allowlist     = _fv("dt-allowlist");
  }
  if (document.getElementById("wecom-enabled")) {
    const c = connectors.wecom;
    c.enabled     = _fc("wecom-enabled", c.enabled);
    c.corp_id     = _fv("wc-corp-id");
    c.corp_secret = _fv("wc-corp-secret");
    c.agent_id    = parseInt(document.getElementById("wc-agent-id")?.value || "0") || 0;
    c.token       = _fv("wc-token");
    c.agent       = _fv("wc-agent") || "default";
    c.allowlist   = _fv("wc-allowlist");
  }

  // System tab
  const maxTokEl    = document.getElementById("sys-max-tokens");
  const maxRndEl    = document.getElementById("sys-max-tool-rounds");
  const syspromptEl = document.getElementById("sys-system-prompt");
  const costInEl    = document.getElementById("sys-cost-in");
  const costOutEl   = document.getElementById("sys-cost-out");
  if (maxTokEl)    wizard.maxTokens     = parseInt(maxTokEl.value)    || wizard.maxTokens;
  if (maxRndEl)    wizard.maxToolRounds = parseInt(maxRndEl.value)    || wizard.maxToolRounds;
  if (syspromptEl) wizard.systemPrompt  = syspromptEl.value;
  if (costInEl)    wizard.costIn        = parseFloat(costInEl.value)  || 0.0;
  if (costOutEl)   wizard.costOut       = parseFloat(costOutEl.value) || 0.0;

  // Browser section (System tab)
  const brEnabledEl = document.getElementById("br-enabled");
  if (brEnabledEl) {
    browser.enabled  = brEnabledEl.checked;
    browser.headless = document.getElementById("br-headless")?.checked ?? browser.headless;
    browser.timeout  = parseInt(document.getElementById("br-timeout")?.value) || browser.timeout;
  }

  // Memory tab
  function _fnum(id, fallback) { const el = document.getElementById(id); return el ? (parseFloat(el.value) || 0) : fallback; }
  function _fint(id, fallback) { const el = document.getElementById(id); return el ? (parseInt(el.value) || fallback) : fallback; }
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
  }

  // Integrations tab — email & calendar
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
  }
}

function validateSettings() {
  const prov = providerById(wizard.provider);
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

function saveSettings() {
  syncFormToState();

  const validationErr = validateSettings();
  if (validationErr) {
    els.wstatus.textContent = "✗ " + validationErr;
    els.wstatus.className = "wstatus err";
    return;
  }

  const prov    = providerById(wizard.provider);
  const model   = wizard.model || prov.defaultModel;
  const baseUrl = (wizard.baseUrl || "").trim() || prov.defaultBaseUrl;

  function parseAllowlistInts(raw) {
    return raw ? raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n)) : [];
  }
  function parseAllowlistStrs(raw) {
    return raw ? raw.split(",").map((s) => s.trim()).filter(Boolean) : [];
  }

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
    allowlist: _intList(_al(tg.allowlist)),
    group_allowlist: _intList(_al(tg.group_allowlist)),
    group_policy: tg.group_policy || "allowlist",
    require_mention: tg.require_mention,
    stream: tg.stream,
  };
  if (tg.bot_token) tgConfig.bot_token = tg.bot_token;

  const fs = connectors.feishu;
  const fsConfig = {
    enabled: fs.enabled, agent: fs.agent || "default",
    allowlist: _strList(_al(fs.allowlist)), stream: fs.stream,
  };
  if (fs.app_id)             fsConfig.app_id             = fs.app_id;
  if (fs.app_secret)         fsConfig.app_secret         = fs.app_secret;
  if (fs.encrypt_key)        fsConfig.encrypt_key        = fs.encrypt_key;
  if (fs.verification_token) fsConfig.verification_token = fs.verification_token;

  const dc = connectors.discord;
  const dcConfig = {
    enabled: dc.enabled, agent: dc.agent || "default",
    allowlist: _intList(_al(dc.allowlist)),
    guild_allowlist: _intList(_al(dc.guild_allowlist)),
    require_mention: dc.require_mention, stream: dc.stream,
  };
  if (dc.bot_token) dcConfig.bot_token = dc.bot_token;

  const sl = connectors.slack;
  const slConfig = {
    enabled: sl.enabled, agent: sl.agent || "default",
    allowlist: _strList(_al(sl.allowlist)), stream: sl.stream,
  };
  if (sl.bot_token) slConfig.bot_token = sl.bot_token;
  if (sl.app_token) slConfig.app_token = sl.app_token;

  const dt = connectors.dingtalk;
  const dtConfig = {
    enabled: dt.enabled, agent: dt.agent || "default",
    allowlist: _strList(_al(dt.allowlist)), stream: dt.stream,
  };
  if (dt.client_id)     dtConfig.client_id     = dt.client_id;
  if (dt.client_secret) dtConfig.client_secret = dt.client_secret;

  const wc = connectors.wecom;
  const wcConfig = {
    enabled: wc.enabled, agent: wc.agent || "default",
    agent_id: wc.agent_id || 0,
    allowlist: _strList(_al(wc.allowlist)),
  };
  if (wc.corp_id)     wcConfig.corp_id     = wc.corp_id;
  if (wc.corp_secret) wcConfig.corp_secret = wc.corp_secret;
  if (wc.token)       wcConfig.token       = wc.token;

  const config = {
    provider: { name: wizard.provider, base_url: baseUrl },
    agent: {
      model,
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
    connectors: {
      telegram: tgConfig, feishu: fsConfig,
      discord: dcConfig, slack: slConfig,
      dingtalk: dtConfig, wecom: wcConfig,
    },
    browser: {
      enabled:  browser.enabled,
      headless: browser.headless,
      timeout:  browser.timeout,
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
      ...(calendarCfg.password ? { password: calendarCfg.password } : {}),
    },
  };
  if (prov.needsKey && wizard.apiKey) config.provider.api_key = wizard.apiKey;
  if (wizard.systemPrompt.trim())     config.agent.system_prompt = wizard.systemPrompt.trim();
  if (wizard.costIn  > 0) config.provider.cost_per_1k_input_tokens  = wizard.costIn;
  if (wizard.costOut > 0) config.provider.cost_per_1k_output_tokens = wizard.costOut;

  wizard.saving = true;
  els.wbtnSave.disabled = true;
  els.wbtnSave.textContent = "⠸ Saving…";
  els.wstatus.textContent = "";
  els.wstatus.className = "wstatus";

  clearTimeout(_wizardSaveTimer);
  _wizardSaveTimer = setTimeout(() => {
    _wizardSaveTimer = null;
    if (!wizard.saving) return;
    wizard.saving = false;
    els.wbtnSave.disabled = false;
    els.wbtnSave.textContent = "💾 Save";
    els.wstatus.textContent = "✗ Request timed out. Check your connection and try again.";
    els.wstatus.className = "wstatus err";
  }, 15000);

  send({ type: "save_config", config });
}

// ── Chat rendering ────────────────────────────────────────────────────────

function appendChunk(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
  pinThinkingMsgToBottom();
  scrollToBottom();
}

function finalizeAiMsg() {
  removeThinkingMsg();
  // Remove the AI bubble if it was created but received no content
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;
}

function insertUserMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("user");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  msgEl.appendChild(bubbleEl);
  return { msgEl, bubbleEl };
}

function insertToolBubble(data) {
  // Discard empty AI bubble if AI jumped straight to a tool call.
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;

  const el = document.createElement("div");
  el.className = "tool-line";
  el.innerHTML = `<span class="tl-name">⚙ ${escHtml(data.tool || "tool")}</span>`
               + `<span class="tl-status">running…</span>`;
  els.messages.appendChild(el);

  if (data.call_id) {
    state._toolBubbles[data.call_id] = el;
  } else if (data.tool) {
    if (!state._toolPendingByName[data.tool]) state._toolPendingByName[data.tool] = [];
    state._toolPendingByName[data.tool].push(el);
  }
  state._toolIndex++;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

function updateToolBubble(data) {
  let el = null;
  if (data.call_id && state._toolBubbles[data.call_id]) {
    el = state._toolBubbles[data.call_id];
  } else if (data.tool && state._toolPendingByName[data.tool]?.length) {
    el = state._toolPendingByName[data.tool].shift();
  }
  if (!el) return;

  const raw = typeof data.result === "string" ? data.result : prettyJson(data.result);
  renderToolResult(el, data.tool || "tool", raw);
}

function renderToolResult(el, toolName, raw) {
  const preview = raw.replace(/\s+/g, " ").trim().slice(0, 100);
  const expandable = raw.length > 100 || raw.includes("\n");
  el.className = "tool-line has-result";
  el.innerHTML = `<span class="tl-name">⚙ ${escHtml(toolName)}</span>`
               + `<span class="tl-result">${escHtml(preview)}</span>`
               + `<span class="tl-done">✓</span>`
               + (expandable ? `<span class="tl-expand">›</span><div class="tl-body">${escHtml(raw)}</div>` : "");
  if (expandable) {
    el.addEventListener("click", () => {
      el.classList.toggle("expanded");
    });
  }
}

// ── Markdown (minimal, XSS-safe) ─────────────────────────────────────────

function renderMarkdown(raw) {
  let s = escHtml(raw);
  // fenced code blocks
  s = s.replace(/```[\w]*\n([\s\S]*?)```/g, (_, inner) => `<code>${inner}</code>`);
  // inline code
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  // **bold**
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  // *italic*
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  // /files/ download links
  s = s.replace(/\/files\/([\w.\-]+)/g, (_, fid) => {
    const apiKey = new URLSearchParams(location.search).get("api_key") || "";
    const href = apiKey ? `/files/${fid}?api_key=${encodeURIComponent(apiKey)}` : `/files/${fid}`;
    const name = fid.includes("_") ? fid.split("_").slice(1).join("_") : fid;
    return `<a class="dl-link" href="${href}" download="${escHtml(name)}">⬇ ${escHtml(name)}</a>`;
  });
  return s;
}

// ── Agents ────────────────────────────────────────────────────────────────

function populateAgents(items) {
  // Store for @mention autocomplete
  state.agents = items.length ? items : [{ name: "default", description: "" }];

  els.agentSelect.innerHTML = "";
  if (!items.length) {
    const opt = document.createElement("option");
    opt.value = "default"; opt.textContent = "default";
    els.agentSelect.appendChild(opt);
    return;
  }
  items.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name + (a.description ? ` — ${a.description}` : "");
    if (a.name === state.agent) opt.selected = true;
    els.agentSelect.appendChild(opt);
  });
}

// ── @mention autocomplete ───────────────────────────────────────────────────

function _getMentionEl() {
  let el = document.getElementById("agent-mention-list");
  if (!el) {
    el = document.createElement("div");
    el.id = "agent-mention-list";
    el.className = "agent-mention-list hidden";
    document.querySelector("footer").insertBefore(el, document.querySelector(".input-row"));
  }
  return el;
}

function showAgentMentionList(query) {
  const q = query.toLowerCase();
  const matches = state.agents.filter(a => a.name.toLowerCase().startsWith(q));
  if (!matches.length) { hideAgentMentionList(); return; }

  state._mentionActive = true;
  state._mentionItems = matches;
  if (state._mentionIndex >= matches.length) state._mentionIndex = 0;

  const el = _getMentionEl();
  el.innerHTML = "";
  matches.forEach((a, i) => {
    const item = document.createElement("div");
    item.className = "mention-item" + (i === state._mentionIndex ? " active" : "");
    item.innerHTML = `<span class="mention-name">@${a.name}</span>${a.description ? `<span class="mention-desc">${a.description}</span>` : ""}`;
    item.addEventListener("mousedown", (ev) => { ev.preventDefault(); selectMentionAgent(a.name); });
    el.appendChild(item);
  });
  el.classList.remove("hidden");
}

function hideAgentMentionList() {
  state._mentionActive = false;
  state._mentionItems = [];
  state._mentionIndex = 0;
  const el = document.getElementById("agent-mention-list");
  if (el) el.classList.add("hidden");
}

function selectMentionAgent(name) {
  // Switch agent
  state.agent = name;
  els.agentSelect.value = name;

  // Replace @query in input with empty string (user continues typing message)
  const val = els.input.value;
  const atIdx = val.lastIndexOf("@");
  if (atIdx !== -1) {
    els.input.value = val.slice(0, atIdx);
  }
  hideAgentMentionList();
  els.input.focus();
  autoResize();
}

// ── Session history restore ────────────────────────────────────────────────

function renderSessionHistory(session_id, turns) {
  // Clear the chat area and reset state before rendering history.
  removeThinkingMsg();
  els.messages.innerHTML = "";
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;

  state.session_id = session_id;
  els.sessionLabel.textContent = `session: ${session_id}`;

  if (!turns.length) {
    insertSystemMsg("No history for this session.");
    return;
  }

  for (const t of turns) {
    if (t.role === "user") {
      insertUserMsg(t.content || "");
    } else if (t.role === "assistant") {
      const { msgEl, bubbleEl } = createMsgBubble("ai");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      els.messages.appendChild(msgEl);
    } else if (t.role === "tool") {
      const el = document.createElement("div");
      renderToolResult(el, t.tool_name || "tool", t.content || "");
      els.messages.appendChild(el);
    }
  }
  scrollToBottom();
}

// ── Sessions sidebar ──────────────────────────────────────────────────────

function loadSession(session_id) {
  state._activeSessionId = session_id;
  document.querySelectorAll(".sidebar-session").forEach((el) => {
    el.classList.toggle("active", el.dataset.sessionId === session_id);
  });
  send({ type: "get_session_history", session_id });
}

function renderSessions(items) {
  const list = document.getElementById("sessions-list");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;font-size:11px">No sessions</div>';
    state._firstSessionLoad = false;
    return;
  }

  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "sidebar-session" + (s.session_id === state._activeSessionId ? " active" : "");
    el.dataset.sessionId = s.session_id;

    const shortId = (s.session_id || "—").slice(-12);
    const lastTs  = s.last_turn ? new Date(s.last_turn * 1000).toLocaleDateString() : "";

    el.innerHTML = `
      <div class="sidebar-session-id" title="${escHtml(s.session_id || "")}">${escHtml(shortId)}</div>
      <div class="sidebar-session-meta">${s.turn_count || 0} turns${lastTs ? " · " + lastTs : ""}</div>
    `;
    el.addEventListener("click", () => loadSession(s.session_id));
    list.appendChild(el);
  });

  // On first load, auto-select the most recent session (items sorted last_turn DESC).
  if (state._firstSessionLoad && items.length) {
    state._firstSessionLoad = false;
    loadSession(items[0].session_id);
  }
}

// ── Memories panel ────────────────────────────────────────────────────────

function renderMemories(items) {
  els.memoriesList.innerHTML = "";
  if (!items.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }
  items.forEach((m) => {
    const noteId = m.id || m.note_id || "";
    const el = document.createElement("div");
    el.className = "list-item";
    el.dataset.noteId = noteId;
    const tags  = (m.tags || []).join(", ") || "—";
    const score = m.score != null ? ` &nbsp;|&nbsp; score: ${m.score.toFixed(2)}` : "";
    const bodyPreview = m.body ? escHtml(m.body.slice(0, 120)) + (m.body.length > 120 ? "…" : "") : "";
    el.innerHTML = `
      <div class="list-item-title">${escHtml(m.title || m.content || m.text || "")}</div>
      ${bodyPreview ? `<div class="list-item-meta">${bodyPreview}</div>` : ""}
      <div class="list-item-meta">ID: ${escHtml(noteId)} &nbsp;|&nbsp; tags: ${escHtml(tags)}${score}</div>
      <div class="list-item-actions">
        <button class="danger" data-note-id="${escHtml(noteId)}">Delete</button>
      </div>
    `;
    el.querySelector(".danger").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const nid = ev.target.dataset.noteId;
      if (!nid || !confirm(`Delete memory ${nid}?`)) return;
      send({ type: "delete_memory", note_id: nid });
    });
    els.memoriesList.appendChild(el);
  });
}

function onMemoryDeleted(noteId, ok) {
  if (!ok) { alert(`Failed to delete memory: ${noteId}`); return; }
  const el = els.memoriesList.querySelector(`[data-note-id="${CSS.escape(noteId)}"]`);
  if (el) el.remove();
  if (!els.memoriesList.children.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
  }
}

// ── UI helpers ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${tab}`);
  });
  const footer = document.querySelector("footer");
  if (footer) footer.style.display = tab === "chat" ? "" : "none";
  if (tab === "memories") send({ type: "list_memories", limit: 20 });
  if (tab === "skills") {
    send({ type: "list_skills" });
    loadSkillMarketplace();
  }
  if (tab === "tasks") {
    send({ type: "list_todos" });
    send({ type: "list_scheduled_tasks" });
    populateSchedAgentSelect();
  }
}

// ── Skills panel ───────────────────────────────────────────────────────────

function loadSkillMarketplace() {
  skills.reposLoading = true;
  skills.reposError = "";
  renderSkillsPanel();
  send({ type: "list_skill_repos" });
}

function handleSkillsList(data) {
  skills.installed = data.items || [];
  skills.skillDir  = data.skill_dir || "";
  skills.configured = Boolean(data.configured);
  if (els.skillDirBadge) {
    els.skillDirBadge.textContent = skills.skillDir
      ? `skill_dir: ${skills.skillDir}`
      : "skill_dir: not configured";
  }
  renderSkillsPanel();
}

function handleSkillRepos(data) {
  skills.reposLoading = false;
  skills.repos = data.items || [];
  skills.reposError = data.error || "";
  renderSkillsPanel();
}

function handleSkillInstallResult(data) {
  skills.installing.delete(data.url);
  if (data.ok) {
    if (data.warning) {
      showSkillToast(`⚠ ${data.repo} cloned — ${data.warning}`, "warn");
    } else {
      const added = data.repo_skill_count != null ? data.repo_skill_count : data.skill_count;
      const toolsMsg = data.bundled_tool_count ? `, ${data.bundled_tool_count} tools loaded` : "";
      const depsMsg = data.deps_installed === false ? " (deps install failed, check manually)" : "";
      showSkillToast(`✓ ${data.repo} installed (${added} new skills${toolsMsg})${depsMsg}`, "ok");
    }
    send({ type: "list_skills" });
    // Refresh repos to update installed badges
    send({ type: "list_skill_repos" });
  } else {
    showSkillToast(`Error: ${data.error}`, "err");
  }
  renderSkillsPanel();
}

function publishSkill(skillName, skillDesc, repoUrl) {
  send({ type: "publish_skill", skill_name: skillName, skill_description: skillDesc || "", repo_url: repoUrl || "" });
}

function handlePublishSkillUrl(data) {
  if (!data.ok) {
    showSkillToast(`Publish error: ${data.error}`, "err");
    return;
  }
  window.open(data.url, "_blank", "noopener");
  showSkillToast(`Opening GitHub to publish "${data.skill_name}"…`, "ok");
}

function installSkillRepo(url) {
  if (!url || skills.installing.has(url)) return;
  skills.installing.add(url);
  renderSkillsPanel();
  send({ type: "install_skill_repo", url });
}

function renderSkillsPanel() {
  if (!els.skillsContent) return;
  const c = els.skillsContent;
  c.innerHTML = "";

  // ── Section 1: Installed Skills ──────────────────────────────
  const sec1 = document.createElement("div");
  sec1.className = "skills-section";

  let installedHtml = `<div class="skills-section-header">Installed Skills <span class="skills-count">${skills.installed.length}</span></div>`;

  if (!skills.configured) {
    installedHtml += `
      <div class="skill-notice">
        <strong>skill_dir not configured.</strong><br>
        Add this to your <code>hushclaw.toml</code> to enable skills:
        <pre>[tools]\nskill_dir = "~/.hushclaw/skills"</pre>
      </div>`;
  } else if (!skills.installed.length) {
    installedHtml += `<div class="empty-state" style="padding:16px 0">No skills installed yet. Browse the marketplace below.</div>`;
  } else {
    installedHtml += `<div class="skills-installed-list">`;
    skills.installed.forEach((s) => {
      installedHtml += `
        <div class="skill-installed-item">
          <div class="skill-installed-meta">
            <span class="skill-name">${escHtml(s.name)}</span>
            ${s.description ? `<span class="skill-desc">${escHtml(s.description)}</span>` : ""}
          </div>
          ${s.builtin ? "" : `<button class="secondary skill-publish-btn" data-name="${escHtml(s.name)}" data-desc="${escHtml(s.description || "")}">Publish</button>`}
        </div>`;
    });
    installedHtml += `</div>`;
  }
  sec1.innerHTML = installedHtml;
  c.appendChild(sec1);

  // ── Section 2: Marketplace ────────────────────────────────────
  const sec2 = document.createElement("div");
  sec2.className = "skills-section";

  let mktHtml = `
    <div class="skills-section-header">
      Skill Marketplace
      <button class="secondary skill-mkt-refresh-btn" id="btn-skill-mkt-refresh">↻ Refresh</button>
    </div>`;

  if (skills.reposLoading) {
    mktHtml += `<div class="empty-state" style="padding:24px 0">Searching GitHub…</div>`;
  } else {
    if (skills.reposError) {
      mktHtml += `<div class="skill-notice skill-notice-warn">GitHub search unavailable (${escHtml(skills.reposError)}). Showing curated repos.</div>`;
    }
    mktHtml += `<div class="skill-repo-list">`;
    skills.repos.forEach((repo) => {
      const installing = skills.installing.has(repo.url);
      const isIndex    = Boolean(repo.note);   // index/list repos have a note
      const btnText    = installing ? "…" : (repo.installed ? "Update" : "Install");
      const btnClass   = repo.installed ? "secondary" : "";
      const curatedBadge = repo.curated ? `<span class="skill-curated-badge">Curated</span>` : "";
      const starsHtml    = repo.stars ? `<div class="stars-badge">★ ${Number(repo.stars).toLocaleString()}</div>` : "";
      const authorHtml   = repo.author ? `<span class="repo-card-author">by ${escHtml(repo.author)}</span>` : "";
      const tagsHtml     = (repo.tags && repo.tags.length)
        ? `<div class="repo-card-tags">${repo.tags.map(t => `<span class="repo-tag">${escHtml(t)}</span>`).join("")}</div>`
        : "";
      mktHtml += `
        <div class="skill-repo-card">
          <div class="repo-card-left">
            <div class="repo-card-name">
              ${curatedBadge}
              <a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener">${escHtml(repo.name)}</a>
              ${authorHtml}
            </div>
            ${repo.description ? `<div class="repo-card-desc">${escHtml(repo.description)}</div>` : ""}
            ${tagsHtml}
            ${repo.note ? `<div class="repo-card-note">ℹ ${escHtml(repo.note)}</div>` : ""}
          </div>
          <div class="repo-card-right">
            ${starsHtml}
            <div class="repo-card-actions">
              ${repo.installed ? '<span class="skill-installed-badge">✓</span>' : ""}
              ${isIndex
                ? `<a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener" class="secondary repo-install-btn">Browse</a>`
                : `<button class="${btnClass} repo-install-btn" data-url="${escHtml(repo.url)}" ${installing ? "disabled" : ""}>${escHtml(btnText)}</button>`
              }
            </div>
          </div>
        </div>`;
    });
    mktHtml += `</div>`;
    if (!skills.repos.length) {
      mktHtml += `<div class="empty-state" style="padding:24px 0">No skill repos found. Use the custom URL below.</div>`;
    }
  }

  // Custom URL row
  mktHtml += `
    <div class="skill-custom-install">
      <div class="skill-custom-label">Add custom repo</div>
      <div class="skill-custom-row">
        <input type="text" id="skill-custom-url"
               placeholder="https://github.com/user/my-skills"
               autocomplete="off">
        <button id="btn-install-custom">Install</button>
      </div>
    </div>`;

  sec2.innerHTML = mktHtml;
  c.appendChild(sec2);

  // Wire events
  document.getElementById("btn-skill-mkt-refresh")
    ?.addEventListener("click", loadSkillMarketplace);

  document.getElementById("btn-install-custom")
    ?.addEventListener("click", () => {
      const url = document.getElementById("skill-custom-url")?.value.trim();
      if (url) installSkillRepo(url);
    });

  document.getElementById("skill-custom-url")
    ?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        const url = ev.target.value.trim();
        if (url) installSkillRepo(url);
      }
    });

  sec2.querySelectorAll(".repo-install-btn").forEach((btn) => {
    btn.addEventListener("click", () => installSkillRepo(btn.dataset.url));
  });

  sec1.querySelectorAll(".skill-publish-btn").forEach((btn) => {
    btn.addEventListener("click", () => publishSkill(btn.dataset.name, btn.dataset.desc, ""));
  });
}

function showSkillToast(msg, kind) {
  const el = document.createElement("div");
  el.className = `skill-toast ${kind}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function setConnStatus(status) {
  els.connStatus.className = `dot ${status}`;
  els.connStatus.title = status.charAt(0).toUpperCase() + status.slice(1);
}

function updateTokenStats() {
  if (state.inTokens || state.outTokens) {
    els.tokenStats.textContent =
      `In: ${state.inTokens.toLocaleString()}  Out: ${state.outTokens.toLocaleString()}`;
  }
}

function setSending(v) {
  state.sending = v;
  els.btnSend.disabled = v || !state.ws || state.ws.readyState !== WebSocket.OPEN;
  els.btnSend.textContent = v ? "⠸" : "Send";
  els.input.disabled = v;
  els.btnStop.classList.toggle("hidden", !v);
}

function insertThinkingMsg() {
  removeThinkingMsg(); // safety
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  bubbleEl.classList.add("thinking-bubble");
  bubbleEl.textContent = "⠋ thinking…";
  els.messages.appendChild(msgEl);
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = Date.now();
  state._thinkingTimer = setInterval(() => {
    if (!state._thinkingEl) return;
    const sec  = Math.floor((Date.now() - state._thinkingStart) / 1000);
    const spin = SPINNERS[_spinIdx++ % SPINNERS.length];
    bubbleEl.textContent = `${spin} thinking ${sec}s`;
  }, 100);
}

function removeThinkingMsg() {
  if (state._thinkingTimer) { clearInterval(state._thinkingTimer); state._thinkingTimer = null; }
  if (state._thinkingEl)    { state._thinkingEl.remove(); state._thinkingEl = null; }
}

// Re-append the thinking bubble to keep it as the last child of the message
// list. appendChild() on an already-attached node moves it without cloning.
function pinThinkingMsgToBottom() {
  if (state._thinkingEl) {
    els.messages.appendChild(state._thinkingEl);
  }
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function prettyJson(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function newSession() {
  removeThinkingMsg();
  state.session_id = null;
  state._activeSessionId = null;
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  els.messages.innerHTML = "";
  els.sessionLabel.textContent = "session: —";
  els.tokenStats.textContent   = "";
  // Clear sidebar highlight
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
  insertSystemMsg("New session started.");
}

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

// ── File upload / attachments ──────────────────────────────────────────────

async function uploadFile(file) {
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  const headers = { "Content-Type": file.type || "application/octet-stream" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  try {
    const res = await fetch(`/upload?name=${encodeURIComponent(file.name)}`,
      { method: "PUT", body: file, headers });
    return await res.json();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function renderAttachmentChips() {
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
    chip.innerHTML = `<span>📄 ${escHtml(att.name)}</span>`;
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

// ── Event listeners ────────────────────────────────────────────────────────

function sendMessage() {
  hideAgentMentionList();
  let text = els.input.value.trim();
  if (!text || state.sending) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  // Parse @agentname prefix — switches active agent for this message.
  const mentionMatch = text.match(/^@(\S+)\s*([\s\S]*)$/);
  if (mentionMatch) {
    const mentionedName = mentionMatch[1];
    const known = state.agents.find(a => a.name === mentionedName);
    if (known) {
      state.agent = known.name;
      els.agentSelect.value = known.name;
      text = mentionMatch[2].trim() || text; // use remainder; fall back to full text if empty
    }
  }

  // Reset tool call/result mapping for the new turn.
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex = 0;

  const attachments = state._attachments.slice();
  state._attachments = [];
  renderAttachmentChips();

  // Build user message display text
  let displayText = els.input.value.trim();
  if (attachments.length) {
    displayText += (displayText ? "\n" : "") + attachments.map(a => `📎 ${a.name}`).join("\n");
  }
  insertUserMsg(displayText);
  els.input.value = "";
  autoResize();
  setSending(true);
  insertThinkingMsg();

  const msg = {
    type:        "chat",
    text,
    agent:       state.agent,
    session_id:  state.session_id || undefined,
  };
  if (attachments.length) msg.attachments = attachments;
  send(msg);
}

els.btnSend.addEventListener("click", sendMessage);

els.btnAttach?.addEventListener("click", () => els.fileInput?.click());

els.fileInput?.addEventListener("change", async () => {
  const files = Array.from(els.fileInput.files || []);
  if (!files.length) return;
  els.fileInput.value = "";  // reset so same file can be re-selected
  for (const file of files) {
    const result = await uploadFile(file);
    if (result.ok) {
      state._attachments.push({ file_id: result.file_id, name: result.name, url: result.url });
      renderAttachmentChips();
    } else {
      insertSystemMsg(`Upload failed: ${result.error || "unknown error"}`);
    }
  }
});

els.btnStop.addEventListener("click", () => {
  if (!state.session_id) return;
  send({ type: "stop", session_id: state.session_id });
  setSending(false);
  insertSystemMsg("Task stopped.");
});

els.btnHandoverDone.addEventListener("click", () => {
  send({ type: "browser_handover_done", session_id: state.session_id });
  els.handoverBanner.classList.add("hidden");
});

els.input.addEventListener("keydown", (ev) => {
  // Handle @mention autocomplete navigation first.
  if (state._mentionActive) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      state._mentionIndex = (state._mentionIndex + 1) % state._mentionItems.length;
      showAgentMentionList(_currentMentionQuery());
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state._mentionIndex = (state._mentionIndex - 1 + state._mentionItems.length) % state._mentionItems.length;
      showAgentMentionList(_currentMentionQuery());
      return;
    }
    if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
      ev.preventDefault();
      const item = state._mentionItems[state._mentionIndex];
      if (item) selectMentionAgent(item.name);
      return;
    }
    if (ev.key === "Escape") {
      hideAgentMentionList();
      return;
    }
  }
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendMessage(); }
});

function _currentMentionQuery() {
  const val = els.input.value;
  const atIdx = val.lastIndexOf("@");
  return atIdx !== -1 ? val.slice(atIdx + 1) : "";
}

els.input.addEventListener("input", () => {
  autoResize();
  // @mention detection: trigger when last word starts with @
  const val = els.input.value;
  const atIdx = val.lastIndexOf("@");
  if (atIdx !== -1 && (atIdx === 0 || /\s/.test(val[atIdx - 1]))) {
    const query = val.slice(atIdx + 1);
    // Only show if no space in the query (i.e. still typing the agent name)
    if (!/\s/.test(query)) {
      showAgentMentionList(query);
      return;
    }
  }
  hideAgentMentionList();
});

els.btnNew.addEventListener("click", newSession);

els.agentSelect.addEventListener("change", () => { state.agent = els.agentSelect.value; });

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

els.btnRefreshSess.addEventListener("click", () => send({ type: "list_sessions" }));

els.btnRefreshSkills?.addEventListener("click", () => {
  send({ type: "list_skills" });
  loadSkillMarketplace();
});

els.btnRefreshMem.addEventListener("click", () => {
  els.memorySearch.value = "";
  send({ type: "list_memories", limit: 20 });
});

els.btnSearchMem.addEventListener("click", () => {
  const q = els.memorySearch.value.trim();
  send({ type: "list_memories", query: q, limit: 20 });
});

els.memorySearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    send({ type: "list_memories", query: els.memorySearch.value.trim(), limit: 20 });
  }
});

// Settings button — fetch fresh config, then open modal
els.btnSettings.addEventListener("click", () => {
  if (!wizard.open) {
    wizard._pendingRefresh = true;
    openWizard(true /* dismissible */);
  }
  send({ type: "get_config_status" });
});

// Settings modal buttons
els.wbtnSave.addEventListener("click", saveSettings);
els.wbtnClose.addEventListener("click", closeWizard);

// Clicking the overlay background does NOT close the modal.
// Use the Close button — avoids accidental dismissal while editing settings.

// ── Tasks panel ───────────────────────────────────────────────────────────

const tasksState = {
  todos: [],
  scheduled: [],
  addingTodo: false,
  todoPriority: false,
  addingSched: false,
};

function renderTodos(items) {
  tasksState.todos = items;
  const el = document.getElementById("todos-list");
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div class="tasks-empty">No todos yet.</div>';
    return;
  }
  // pending first, done at bottom
  const pending = items.filter(t => t.status !== "done");
  const done = items.filter(t => t.status === "done");
  el.innerHTML = "";
  [...pending, ...done].forEach(todo => {
    el.appendChild(buildTodoRow(todo));
  });
}

function buildTodoRow(todo) {
  const row = document.createElement("div");
  row.className = "todo-row" + (todo.status === "done" ? " done" : "") + (todo.priority ? " high-priority" : "");
  row.dataset.id = todo.todo_id;

  const check = document.createElement("button");
  check.className = "todo-check" + (todo.status === "done" ? " checked" : "");
  check.textContent = todo.status === "done" ? "☑" : "☐";
  check.title = todo.status === "done" ? "Mark as pending" : "Mark as done";
  check.addEventListener("click", () => {
    const newStatus = todo.status === "done" ? "pending" : "done";
    send({ type: "update_todo", todo_id: todo.todo_id, status: newStatus });
  });

  const title = document.createElement("span");
  title.className = "todo-title";
  title.textContent = todo.title;

  const meta = document.createElement("span");
  meta.className = "todo-meta";
  if (todo.priority) {
    const badge = document.createElement("span");
    badge.className = "priority-badge";
    badge.textContent = "!";
    meta.appendChild(badge);
  }
  if (todo.due_at) {
    const due = document.createElement("span");
    due.className = "todo-due";
    const d = new Date(todo.due_at * 1000);
    due.textContent = d.toLocaleDateString();
    meta.appendChild(due);
  }

  const del = document.createElement("button");
  del.className = "todo-del icon-btn secondary";
  del.textContent = "✕";
  del.title = "Delete todo";
  del.addEventListener("click", () => {
    send({ type: "delete_todo", todo_id: todo.todo_id });
    row.remove();
  });

  row.appendChild(check);
  row.appendChild(title);
  row.appendChild(meta);
  row.appendChild(del);
  return row;
}

function onTodoCreated(item) {
  if (!item) return;
  tasksState.todos.push(item);
  renderTodos(tasksState.todos);
}

function onTodoUpdated(item) {
  if (!item) return;
  const idx = tasksState.todos.findIndex(t => t.todo_id === item.todo_id);
  if (idx >= 0) tasksState.todos[idx] = item;
  else tasksState.todos.push(item);
  renderTodos(tasksState.todos);
}

function onTodoDeleted(todo_id, ok) {
  if (ok) {
    tasksState.todos = tasksState.todos.filter(t => t.todo_id !== todo_id);
    renderTodos(tasksState.todos);
  }
}

function renderScheduledTasks(tasks) {
  tasksState.scheduled = tasks;
  const el = document.getElementById("scheduled-list");
  if (!el) return;
  if (!tasks.length) {
    el.innerHTML = '<div class="tasks-empty">No scheduled tasks yet.</div>';
    return;
  }
  el.innerHTML = "";
  tasks.forEach(task => el.appendChild(buildSchedRow(task)));
}

function buildSchedRow(task) {
  const row = document.createElement("div");
  row.className = "sched-row" + (task.enabled ? "" : " disabled");
  row.dataset.id = task.id;

  const icon = document.createElement("span");
  icon.className = "sched-icon";
  icon.textContent = task.run_once ? "⚡" : "⟳";
  icon.title = task.run_once ? "One-shot task" : "Recurring task";

  const info = document.createElement("div");
  info.className = "sched-info";
  const name = document.createElement("span");
  name.className = "sched-name";
  name.textContent = task.title || task.prompt.slice(0, 50);
  const cronSpan = document.createElement("span");
  cronSpan.className = "sched-cron";
  cronSpan.textContent = task.cron;
  info.appendChild(name);
  info.appendChild(cronSpan);

  // Expand to show prompt on click
  name.style.cursor = "pointer";
  name.addEventListener("click", () => {
    const existing = row.querySelector(".sched-prompt-preview");
    if (existing) { existing.remove(); return; }
    const pre = document.createElement("div");
    pre.className = "sched-prompt-preview";
    pre.textContent = task.prompt;
    info.appendChild(pre);
  });

  const actions = document.createElement("div");
  actions.className = "sched-actions";

  const toggleBtn = document.createElement("button");
  toggleBtn.className = "secondary small";
  toggleBtn.textContent = task.enabled ? "⏸" : "▶";
  toggleBtn.title = task.enabled ? "Pause" : "Resume";
  toggleBtn.addEventListener("click", () => {
    send({ type: "toggle_scheduled_task", task_id: task.id, enabled: !task.enabled });
  });

  const runBtn = document.createElement("button");
  runBtn.className = "secondary small";
  runBtn.textContent = "▷";
  runBtn.title = "Run now";
  runBtn.addEventListener("click", () => {
    send({ type: "run_scheduled_task_now", task_id: task.id });
    runBtn.textContent = "…";
    setTimeout(() => { runBtn.textContent = "▷"; }, 2000);
  });

  const delBtn = document.createElement("button");
  delBtn.className = "danger small";
  delBtn.textContent = "✕";
  delBtn.title = "Delete";
  delBtn.addEventListener("click", () => {
    send({ type: "delete_scheduled_task", task_id: task.id });
    row.remove();
  });

  actions.appendChild(toggleBtn);
  actions.appendChild(runBtn);
  actions.appendChild(delBtn);

  row.appendChild(icon);
  row.appendChild(info);
  row.appendChild(actions);
  return row;
}

function onTaskCreated(task) {
  if (!task) return;
  tasksState.scheduled.push(task);
  renderScheduledTasks(tasksState.scheduled);
}

function onTaskToggled(task_id, enabled, ok) {
  if (!ok) return;
  const t = tasksState.scheduled.find(t => t.id === task_id);
  if (t) {
    t.enabled = enabled ? 1 : 0;
    renderScheduledTasks(tasksState.scheduled);
  }
}

function populateSchedAgentSelect() {
  const sel = document.getElementById("sched-agent-select");
  if (!sel) return;
  sel.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "default";
  sel.appendChild(defaultOpt);
  state.agents.forEach(a => {
    if (a.name === "default") return;
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  });
}

function buildCronFromSimple() {
  const freq = document.getElementById("sched-freq")?.value || "daily";
  const time = document.getElementById("sched-time")?.value || "09:00";
  const [h, m] = time.split(":").map(Number);
  if (freq === "hourly") return `${m} * * * *`;
  if (freq === "weekly") return `${m} ${h} * * 1`;
  return `${m} ${h} * * *`; // daily
}

// ── Tasks panel — event listeners ─────────────────────────────────────────

document.getElementById("btn-add-todo")?.addEventListener("click", () => {
  const row = document.getElementById("todo-add-row");
  if (!row) return;
  tasksState.addingTodo = true;
  tasksState.todoPriority = false;
  row.classList.remove("hidden");
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-title-input")?.focus();
});

document.getElementById("btn-todo-cancel")?.addEventListener("click", () => {
  document.getElementById("todo-add-row")?.classList.add("hidden");
  tasksState.addingTodo = false;
});

document.getElementById("todo-priority-btn")?.addEventListener("click", (e) => {
  tasksState.todoPriority = !tasksState.todoPriority;
  e.target.classList.toggle("active", tasksState.todoPriority);
});

document.getElementById("btn-todo-submit")?.addEventListener("click", submitTodo);

document.getElementById("todo-title-input")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") submitTodo();
  if (ev.key === "Escape") document.getElementById("btn-todo-cancel")?.click();
});

function submitTodo() {
  const titleEl = document.getElementById("todo-title-input");
  const dueEl = document.getElementById("todo-due-input");
  const title = titleEl?.value.trim();
  if (!title) return;
  let due_at = null;
  if (dueEl?.value) {
    due_at = Math.floor(new Date(dueEl.value + "T00:00:00").getTime() / 1000);
  }
  send({
    type: "create_todo",
    title,
    priority: tasksState.todoPriority ? 1 : 0,
    due_at,
    tags: [],
  });
  if (titleEl) titleEl.value = "";
  if (dueEl) dueEl.value = "";
  tasksState.todoPriority = false;
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-add-row")?.classList.add("hidden");
}

document.getElementById("btn-add-scheduled")?.addEventListener("click", () => {
  const row = document.getElementById("sched-add-row");
  if (!row) return;
  row.classList.remove("hidden");
  populateSchedAgentSelect();
  document.getElementById("sched-title-input")?.focus();
});

document.getElementById("btn-sched-cancel")?.addEventListener("click", () => {
  document.getElementById("sched-add-row")?.classList.add("hidden");
});

document.querySelectorAll("input[name='sched-mode']").forEach(radio => {
  radio.addEventListener("change", () => {
    const isCron = radio.value === "cron";
    document.getElementById("sched-simple-inputs")?.classList.toggle("hidden", isCron);
    document.getElementById("sched-cron-input")?.classList.toggle("hidden", !isCron);
  });
});

document.getElementById("btn-sched-submit")?.addEventListener("click", submitSchedTask);

function submitSchedTask() {
  const title = document.getElementById("sched-title-input")?.value.trim() || "";
  const prompt = document.getElementById("sched-prompt-input")?.value.trim() || "";
  if (!prompt) return;
  const modeEl = document.querySelector("input[name='sched-mode']:checked");
  const mode = modeEl?.value || "simple";
  let cron;
  if (mode === "cron") {
    cron = document.getElementById("sched-cron-expr")?.value.trim() || "0 9 * * *";
  } else {
    cron = buildCronFromSimple();
  }
  const agent = document.getElementById("sched-agent-select")?.value || "";
  const runOnce = document.getElementById("sched-run-once")?.checked || false;
  send({ type: "create_scheduled_task", title, cron, prompt, agent, run_once: runOnce });
  // Reset form
  const titleEl = document.getElementById("sched-title-input");
  const promptEl = document.getElementById("sched-prompt-input");
  if (titleEl) titleEl.value = "";
  if (promptEl) promptEl.value = "";
  document.getElementById("sched-add-row")?.classList.add("hidden");
}

// ── Boot ──────────────────────────────────────────────────────────────────

insertSystemMsg("Connecting to HushClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";
connect();
