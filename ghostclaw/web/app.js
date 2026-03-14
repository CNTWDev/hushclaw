/**
 * GhostClaw Web UI — app.js
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
};

// ── Pending-request timers (reset on WS reconnect) ─────────────────────────

let _wizardSaveTimer = null;   // fires if config_saved never arrives
let _testTimer       = null;   // fires if test_provider_result never arrives

// ── Settings modal state ────────────────────────────────────────────────────

const wizard = {
  tab: "model",           // "model" | "channels" | "system"
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
  {
    id: "anthropic-sdk",
    name: "Anthropic SDK",
    desc: "Anthropic via the official Python SDK (requires pip install anthropic).",
    needsKey: true,
    defaultModel: "claude-sonnet-4-6",
    modelSuggestions: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    keyLabel: "Anthropic API Key",
    keyPlaceholder: "sk-ant-api03-…",
    keyHint: 'Requires: <code>pip install ghostclaw[anthropic]</code>',
    defaultBaseUrl: "",
    baseUrlLabel: "Base URL (optional)",
  },
];

function providerById(id) {
  // Normalise legacy / merged provider IDs to their current canonical ID
  const ALIASES = { "openai-raw": "openai-sdk", "aigocode-raw": "anthropic-raw", "aigocode": "anthropic-raw" };
  const normalised = ALIASES[id] || id;
  return PROVIDERS.find((p) => p.id === normalised) || PROVIDERS[0];
}

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

const els = {
  agentSelect:      $("agent-select"),
  messages:         $("messages"),
  input:            $("input"),
  btnSend:          $("btn-send"),
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
    enabled: false,
    bot_token: "",
    bot_token_set: false,
    agent: "default",
    allowlist: "",
    stream: true,
  },
  feishu: {
    enabled: false,
    app_id: "",
    app_id_set: false,
    app_secret: "",
    app_secret_set: false,
    agent: "default",
    allowlist: "",
    stream: false,
  },
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
    case "test_provider_result":
      handleTestProviderResult(data);
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
    // Re-render the modal with fresh data if it's already open
    if (wizard.open) renderSettingsModal();
  }

  // Always populate connectors state (used in channels tab)
  if (cfg.connectors) {
    const tg = cfg.connectors.telegram || {};
    connectors.telegram.enabled       = Boolean(tg.enabled);
    connectors.telegram.bot_token     = "";
    connectors.telegram.bot_token_set = Boolean(tg.bot_token_set);
    connectors.telegram.agent         = tg.agent || "default";
    connectors.telegram.allowlist     = (tg.allowlist || []).join(", ");
    connectors.telegram.stream        = tg.stream !== false;
    const fs = cfg.connectors.feishu || {};
    connectors.feishu.enabled         = Boolean(fs.enabled);
    connectors.feishu.app_id          = "";
    connectors.feishu.app_id_set      = Boolean(fs.app_id_set);
    connectors.feishu.app_secret      = "";
    connectors.feishu.app_secret_set  = Boolean(fs.app_secret_set);
    connectors.feishu.agent           = fs.agent || "default";
    connectors.feishu.allowlist       = (fs.allowlist || []).join(", ");
    connectors.feishu.stream          = Boolean(fs.stream);
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
    { id: "model",    label: "🤖 Model" },
    { id: "channels", label: "📡 Channels" },
    { id: "system",   label: "⚙ System" },
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
    case "model":    renderModelTab();    break;
    case "channels": renderChannelsTab(); break;
    case "system":   renderSystemTab();   break;
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

function renderChannelsTab() {
  const tg = connectors.telegram;
  const fs = connectors.feishu;

  function tokenHint(isSet) {
    return isSet ? '<span class="conn-set-badge">set</span> Leave blank to keep current.' : "Not yet configured.";
  }

  els.wizardBody.innerHTML = `
    <div class="conn-panel">
      <div class="conn-section">
        <div class="conn-section-header">
          <span class="conn-platform-icon">✈</span>
          <span class="conn-platform-name">Telegram Bot</span>
          <label class="toggle-switch">
            <input type="checkbox" id="tg-enabled" ${tg.enabled ? "checked" : ""}>
            <span class="toggle-slider"></span>
          </label>
        </div>
        <div class="conn-fields" id="tg-fields" style="${tg.enabled ? "" : "display:none"}">
          <div class="wfield">
            <label>Bot Token</label>
            <input type="password" id="tg-token" autocomplete="off"
                   placeholder="123456:ABCDEF…" value="${escHtml(tg.bot_token)}">
            <div class="wfield-hint">${tokenHint(tg.bot_token_set)}
              Get one from <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>.
            </div>
          </div>
          <div class="wfield">
            <label>Agent</label>
            <input type="text" id="tg-agent" value="${escHtml(tg.agent)}" placeholder="default">
            <div class="wfield-hint">GhostClaw agent name to route messages to.</div>
          </div>
          <div class="wfield">
            <label>User Allowlist <span class="wfield-optional">(optional)</span></label>
            <input type="text" id="tg-allowlist" value="${escHtml(tg.allowlist)}"
                   placeholder="123456789, 987654321">
            <div class="wfield-hint">Comma-separated Telegram user IDs. Leave empty to allow everyone.</div>
          </div>
          <div class="wfield wfield-row">
            <label>Streaming replies</label>
            <label class="toggle-switch toggle-inline">
              <input type="checkbox" id="tg-stream" ${tg.stream ? "checked" : ""}>
              <span class="toggle-slider"></span>
            </label>
            <div class="wfield-hint">Edit message progressively as text arrives (simulates streaming).</div>
          </div>
        </div>
      </div>

      <div class="conn-section">
        <div class="conn-section-header">
          <span class="conn-platform-icon">🪁</span>
          <span class="conn-platform-name">Feishu / Lark</span>
          <label class="toggle-switch">
            <input type="checkbox" id="fs-enabled" ${fs.enabled ? "checked" : ""}>
            <span class="toggle-slider"></span>
          </label>
        </div>
        <div class="conn-fields" id="fs-fields" style="${fs.enabled ? "" : "display:none"}">
          <div class="wfield">
            <label>App ID</label>
            <input type="text" id="fs-appid" autocomplete="off"
                   placeholder="cli_xxxxxxxxxx" value="${escHtml(fs.app_id)}">
            <div class="wfield-hint">${tokenHint(fs.app_id_set)}
              Found in Feishu Open Platform → App credentials.
            </div>
          </div>
          <div class="wfield">
            <label>App Secret</label>
            <input type="password" id="fs-secret" autocomplete="off"
                   placeholder="App Secret" value="${escHtml(fs.app_secret)}">
            <div class="wfield-hint">${tokenHint(fs.app_secret_set)}</div>
          </div>
          <div class="wfield">
            <label>Agent</label>
            <input type="text" id="fs-agent" value="${escHtml(fs.agent)}" placeholder="default">
          </div>
          <div class="wfield">
            <label>Chat Allowlist <span class="wfield-optional">(optional)</span></label>
            <input type="text" id="fs-allowlist" value="${escHtml(fs.allowlist)}"
                   placeholder="oc_xxxxxxxx, oc_yyyyyyyy">
            <div class="wfield-hint">Comma-separated Feishu chat IDs. Leave empty to allow all.</div>
          </div>
          <div class="wfield wfield-row">
            <label>Streaming replies</label>
            <label class="toggle-switch toggle-inline">
              <input type="checkbox" id="fs-stream" ${fs.stream ? "checked" : ""}>
              <span class="toggle-slider"></span>
            </label>
            <div class="wfield-hint">Requires Interactive Card permissions. Disable unless you have them.</div>
          </div>
        </div>
      </div>
    </div>
  `;

  document.getElementById("tg-enabled").addEventListener("change", (e) => {
    document.getElementById("tg-fields").style.display = e.target.checked ? "" : "none";
  });
  document.getElementById("fs-enabled").addEventListener("change", (e) => {
    document.getElementById("fs-fields").style.display = e.target.checked ? "" : "none";
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
                  placeholder="You are GhostClaw, a helpful AI assistant…">${escHtml(wizard.systemPrompt)}</textarea>
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
        GhostClaw does not control provider-side rate limits or credit quotas.
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
  `;
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

  // Channels tab
  const tgEnabledEl = document.getElementById("tg-enabled");
  if (tgEnabledEl) {
    connectors.telegram.enabled   = tgEnabledEl.checked;
    connectors.telegram.bot_token = (document.getElementById("tg-token")?.value ?? "").trim();
    connectors.telegram.agent     = (document.getElementById("tg-agent")?.value ?? "default").trim() || "default";
    connectors.telegram.allowlist = (document.getElementById("tg-allowlist")?.value ?? "").trim();
    connectors.telegram.stream    = document.getElementById("tg-stream")?.checked ?? connectors.telegram.stream;
  }
  const fsEnabledEl = document.getElementById("fs-enabled");
  if (fsEnabledEl) {
    connectors.feishu.enabled    = fsEnabledEl.checked;
    connectors.feishu.app_id     = (document.getElementById("fs-appid")?.value  ?? "").trim();
    connectors.feishu.app_secret = (document.getElementById("fs-secret")?.value ?? "").trim();
    connectors.feishu.agent      = (document.getElementById("fs-agent")?.value  ?? "default").trim() || "default";
    connectors.feishu.allowlist  = (document.getElementById("fs-allowlist")?.value ?? "").trim();
    connectors.feishu.stream     = document.getElementById("fs-stream")?.checked ?? connectors.feishu.stream;
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

  const tg = connectors.telegram;
  const fs = connectors.feishu;
  const tgConfig = {
    enabled: tg.enabled,
    agent:   tg.agent || "default",
    allowlist: parseAllowlistInts(typeof tg.allowlist === "string" ? tg.allowlist : (tg.allowlist || []).join(", ")),
    stream:  tg.stream,
  };
  if (tg.bot_token) tgConfig.bot_token = tg.bot_token;

  const fsConfig = {
    enabled: fs.enabled,
    agent:   fs.agent || "default",
    allowlist: parseAllowlistStrs(typeof fs.allowlist === "string" ? fs.allowlist : (fs.allowlist || []).join(", ")),
    stream:  fs.stream,
  };
  if (fs.app_id)     fsConfig.app_id     = fs.app_id;
  if (fs.app_secret) fsConfig.app_secret = fs.app_secret;

  const config = {
    provider: { name: wizard.provider, base_url: baseUrl },
    agent: {
      model,
      max_tokens:     wizard.maxTokens,
      max_tool_rounds: wizard.maxToolRounds,
    },
    connectors: { telegram: tgConfig, feishu: fsConfig },
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
  return s;
}

// ── Agents ────────────────────────────────────────────────────────────────

function populateAgents(items) {
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

// ── Sessions panel ────────────────────────────────────────────────────────

function renderSessions(items) {
  els.sessionsList.innerHTML = "";
  if (!items.length) {
    els.sessionsList.innerHTML = '<div class="empty-state">No sessions found.</div>';
    return;
  }
  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "list-item";
    const inTok  = (s.total_input_tokens  || 0).toLocaleString();
    const outTok = (s.total_output_tokens || 0).toLocaleString();
    el.innerHTML = `
      <div class="list-item-title">${escHtml(s.session_id || "—")}</div>
      <div class="list-item-meta">Turns: ${s.turn_count || 0} &nbsp;|&nbsp; In: ${inTok} &nbsp;|&nbsp; Out: ${outTok}</div>
      ${s.last_turn ? `<div class="list-item-meta">Last: ${escHtml(String(s.last_turn))}</div>` : ""}
    `;
    el.style.cursor = "pointer";
    el.addEventListener("click", () => {
      send({ type: "get_session_history", session_id: s.session_id });
      switchTab("chat");
    });
    els.sessionsList.appendChild(el);
  });
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
  if (tab === "sessions") send({ type: "list_sessions" });
  if (tab === "memories") send({ type: "list_memories", limit: 20 });
  if (tab === "skills") {
    send({ type: "list_skills" });
    loadSkillMarketplace();
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
      showSkillToast(`✓ ${data.repo} installed (${added} new skills, ${data.skill_count} total)`, "ok");
    }
    send({ type: "list_skills" });
    // Refresh repos to update installed badges
    send({ type: "list_skill_repos" });
  } else {
    showSkillToast(`Error: ${data.error}`, "err");
  }
  renderSkillsPanel();
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
        Add this to your <code>ghostclaw.toml</code> to enable skills:
        <pre>[tools]\nskill_dir = "~/.ghostclaw/skills"</pre>
      </div>`;
  } else if (!skills.installed.length) {
    installedHtml += `<div class="empty-state" style="padding:16px 0">No skills installed yet. Browse the marketplace below.</div>`;
  } else {
    installedHtml += `<div class="skills-installed-list">`;
    skills.installed.forEach((s) => {
      installedHtml += `
        <div class="skill-installed-item">
          <span class="skill-name">${escHtml(s.name)}</span>
          ${s.description ? `<span class="skill-desc">${escHtml(s.description)}</span>` : ""}
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
      mktHtml += `
        <div class="skill-repo-card">
          <div class="repo-card-left">
            <div class="repo-card-name">
              ${curatedBadge}
              <a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener">${escHtml(repo.name)}</a>
            </div>
            ${repo.description ? `<div class="repo-card-desc">${escHtml(repo.description)}</div>` : ""}
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
  insertSystemMsg("New session started.");
}

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

// ── Event listeners ────────────────────────────────────────────────────────

function sendMessage() {
  const text = els.input.value.trim();
  if (!text || state.sending) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  // Reset tool call/result mapping for the new turn.
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex = 0;

  insertUserMsg(text);
  els.input.value = "";
  autoResize();
  setSending(true);
  insertThinkingMsg();

  send({
    type:       "chat",
    text,
    agent:      state.agent,
    session_id: state.session_id || undefined,
  });
}

els.btnSend.addEventListener("click", sendMessage);

els.input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendMessage(); }
});

els.input.addEventListener("input", autoResize);

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

// ── Boot ──────────────────────────────────────────────────────────────────

insertSystemMsg("Connecting to GhostClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";
connect();
