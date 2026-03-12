/**
 * GhostClaw Web UI — app.js
 * Pure JS, no build step, no external dependencies.
 */

"use strict";

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
  _toolIndex: 0,
  // current streaming AI bubble
  _aiMsgEl: null,
  _aiBubbleEl: null,
};

// ── Wizard state ───────────────────────────────────────────────────────────

const wizard = {
  step: 1,
  totalSteps: 4,
  // collected values
  provider: "anthropic-raw",
  apiKey: "",
  baseUrl: "",
  model: "claude-sonnet-4-6",
  // config received from server
  serverConfig: null,
  // whether currently open
  open: false,
};

// Provider definitions
const PROVIDERS = [
  {
    id: "anthropic-raw",
    name: "Anthropic",
    desc: "Claude models (Opus, Sonnet, Haiku). Recommended. Uses urllib — no extra deps.",
    needsKey: true,
    defaultModel: "claude-sonnet-4-6",
    modelSuggestions: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    keyLabel: "Anthropic API Key",
    keyPlaceholder: "sk-ant-api03-…",
    keyHint: 'Get your key at <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a>',
    defaultBaseUrl: "https://api.anthropic.com/v1",
    baseUrlLabel: "Base URL (optional — override for proxies)",
  },
  {
    id: "openai-raw",
    name: "OpenAI / Compatible",
    desc: "GPT-4o, GPT-4, or any OpenAI-compatible endpoint (Groq, Together, etc.).",
    needsKey: true,
    defaultModel: "gpt-4o",
    modelSuggestions: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    keyLabel: "API Key",
    keyPlaceholder: "sk-…",
    keyHint: 'Get your key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com</a>',
    defaultBaseUrl: "https://api.openai.com/v1",
    baseUrlLabel: "Base URL (change for compatible endpoints)",
  },
  {
    id: "aigocode-raw",
    name: "AIGOCODE",
    desc: "AIGOCODE relay — Anthropic-compatible proxy. Supports Claude models.",
    needsKey: true,
    defaultModel: "claude-sonnet-4-6",
    modelSuggestions: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    keyLabel: "AIGOCODE API Key",
    keyPlaceholder: "sk-…",
    keyHint: "Use the API key generated in your AIGOCODE dashboard.",
    defaultBaseUrl: "https://api.aigocode.com/v1",
    baseUrlLabel: "AIGOCODE Base URL",
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
  return PROVIDERS.find((p) => p.id === id) || PROVIDERS[0];
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
  // wizard
  wizardOverlay:    $("wizard-overlay"),
  wizardBody:       $("wizard-body"),
  wizardProgress:   $("wizard-progress"),
  wbtnBack:         $("wbtn-back"),
  wbtnNext:         $("wbtn-next"),
  wbtnSave:         $("wbtn-save"),
  wbtnSkip:         $("wbtn-skip"),
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
  }
}

// ── Setup wizard ───────────────────────────────────────────────────────────

function handleConfigStatus(cfg) {
  wizard.serverConfig = cfg;
  if (!wizard.open) {
    // Only update wizard fields when the wizard is closed, to avoid
    // config_status responses overwriting edits the user is making.
    const prov = providerById(cfg.provider);
    wizard.provider = prov.id;
    wizard.model    = cfg.model || prov.defaultModel;
    wizard.baseUrl  = cfg.base_url || prov.defaultBaseUrl || "";
    wizard.apiKey   = "";
  }

  if (!cfg.configured && !wizard.open) {
    openWizard(false /* not dismissible */);
  }
}

function handleConfigSaved(data) {
  if (!data.ok) {
    // show error in wizard
    const err = wizardEl("wiz-save-error");
    if (err) err.textContent = "Save failed: " + (data.error || "unknown error");
    return;
  }
  // Show success screen
  renderWizardSuccess(data.config_file);
}

function openWizard(dismissible = true) {
  wizard.open = true;
  wizard.step = 1;
  els.wizardOverlay.classList.remove("hidden");
  els.wbtnSkip.style.display = dismissible ? "" : "none";
  renderWizardStep();
}

function closeWizard() {
  wizard.open = false;
  els.wizardOverlay.classList.add("hidden");
}

function wizardEl(id) {
  return document.getElementById(id);
}

// Build progress dots
function renderWizardProgress() {
  els.wizardProgress.innerHTML = "";
  for (let i = 1; i <= wizard.totalSteps; i++) {
    const dot = document.createElement("span");
    dot.className = "wprog-dot" +
      (i === wizard.step ? " active" : i < wizard.step ? " done" : "");
    els.wizardProgress.appendChild(dot);
  }
}

function renderWizardStep() {
  renderWizardProgress();

  // Footer buttons
  els.wbtnBack.style.display = wizard.step > 1 ? "" : "none";
  els.wbtnNext.style.display = wizard.step < wizard.totalSteps ? "" : "none";
  els.wbtnSave.style.display = wizard.step === wizard.totalSteps ? "" : "none";

  switch (wizard.step) {
    case 1: renderStep1(); break;
    case 2: renderStep2(); break;
    case 3: renderStep3(); break;
    case 4: renderStep4(); break;
  }
}

// Step 1 — Choose provider
function renderStep1() {
  let html = `
    <h2>Choose your AI Provider</h2>
    <p class="wdesc">GhostClaw supports multiple LLM backends. Pick the one you have access to.</p>
    <div class="provider-cards" id="provider-cards">
  `;
  PROVIDERS.forEach((p) => {
    const sel = p.id === wizard.provider ? " selected" : "";
    html += `
      <label class="provider-card${sel}" data-id="${p.id}">
        <input type="radio" name="provider" value="${p.id}" ${sel ? "checked" : ""}>
        <div class="provider-card-info">
          <div class="provider-card-name">${escHtml(p.name)}</div>
          <div class="provider-card-desc">${escHtml(p.desc)}</div>
        </div>
      </label>
    `;
  });
  html += `</div>`;
  els.wizardBody.innerHTML = html;

  // Wire radio change
  els.wizardBody.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      wizard.provider = radio.value;
      const prov = providerById(wizard.provider);
      wizard.model = prov.defaultModel;
      wizard.baseUrl = prov.defaultBaseUrl || "";
      // Update card highlighting
      els.wizardBody.querySelectorAll(".provider-card").forEach((c) => {
        c.classList.toggle("selected", c.dataset.id === wizard.provider);
      });
    });
  });

  // Click on card label also selects
  els.wizardBody.querySelectorAll(".provider-card").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector("input[type=radio]");
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
    });
  });
}

// Step 2 — API key + base URL
function renderStep2() {
  const prov = providerById(wizard.provider);
  let html = `<h2>API Key &amp; Endpoint</h2>`;

  if (prov.needsKey) {
    html += `
      <div class="wfield">
        <label>${escHtml(prov.keyLabel)}</label>
        <input type="password" id="wiz-apikey" placeholder="${escHtml(prov.keyPlaceholder)}"
               autocomplete="off" value="${escHtml(wizard.apiKey)}">
        <div class="wfield-hint">${prov.keyHint}</div>
      </div>
    `;
  } else {
    html += `<p class="wdesc">${prov.keyHint}</p>`;
  }

  if (prov.baseUrlLabel) {
    const burl = wizard.baseUrl || prov.defaultBaseUrl;
    html += `
      <div class="wfield">
        <label>${escHtml(prov.baseUrlLabel)}</label>
        <input type="text" id="wiz-baseurl" placeholder="${escHtml(prov.defaultBaseUrl)}"
               value="${escHtml(burl)}">
        <div class="wfield-hint">Leave as-is unless you're using a proxy or custom endpoint.</div>
      </div>
    `;
  }

  els.wizardBody.innerHTML = html;

  // Live sync to wizard state
  const keyEl  = wizardEl("wiz-apikey");
  const burlEl = wizardEl("wiz-baseurl");
  if (keyEl)  keyEl.addEventListener("input",  () => { wizard.apiKey  = keyEl.value.trim(); });
  if (burlEl) burlEl.addEventListener("input", () => { wizard.baseUrl = burlEl.value.trim(); });
}

// Step 3 — Model selection (with dynamic listing)
function renderStep3() {
  const prov = providerById(wizard.provider);
  const suggestions = prov.modelSuggestions;
  const currentModel = wizard.model || prov.defaultModel;
  const listId = "wiz-model-list";

  let optionsHtml = suggestions.map((m) => `<option value="${escHtml(m)}">`).join("");

  els.wizardBody.innerHTML = `
    <h2>Select Model</h2>
    <p class="wdesc">
      Choose the model for <strong>${escHtml(prov.name)}</strong>.
      You can type any model name supported by the provider.
    </p>
    <div class="wfield">
      <label>Model name</label>
      <span id="wiz-model-loading" class="muted" style="font-size:12px">Fetching available models…</span>
      <select id="wiz-model-select" style="display:none"></select>
      <input type="text" id="wiz-model" list="${listId}"
             placeholder="${escHtml(prov.defaultModel)}"
             value="${escHtml(currentModel)}">
      <datalist id="${listId}">${optionsHtml}</datalist>
      <div class="wfield-hint">Select from list or type any model ID.</div>
    </div>
    <div style="margin-top:16px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px">Quick pick</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        ${suggestions.map((m) => `<button type="button" class="secondary model-chip" data-model="${escHtml(m)}">${escHtml(m)}</button>`).join("")}
      </div>
    </div>
  `;

  const modelEl = wizardEl("wiz-model");
  const selectEl = document.getElementById("wiz-model-select");

  modelEl.addEventListener("input", () => { wizard.model = modelEl.value.trim(); });
  if (selectEl) {
    selectEl.addEventListener("change", () => {
      wizard.model = selectEl.value;
      modelEl.value = selectEl.value;
    });
  }

  els.wizardBody.querySelectorAll(".model-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      wizard.model = chip.dataset.model;
      modelEl.value = wizard.model;
      if (selectEl && selectEl.style.display !== "none") {
        selectEl.value = wizard.model;
      }
    });
  });

  // Request dynamic model list from server
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: "list_models",
      provider: wizard.provider,
      api_key: wizard.apiKey,
      base_url: wizard.baseUrl || prov.defaultBaseUrl,
    }));
  } else {
    document.getElementById("wiz-model-loading")?.remove();
  }
}

function handleModelsResponse(msg) {
  if (!wizard.open || wizard.step !== 3) return;
  const loadingEl = document.getElementById("wiz-model-loading");
  const selectEl  = document.getElementById("wiz-model-select");
  const inputEl   = document.getElementById("wiz-model");

  if (loadingEl) loadingEl.remove();

  if (msg.items && msg.items.length > 0) {
    const currentVal = wizard.model || providerById(wizard.provider).defaultModel;
    let opts = "";
    // Prepend currentVal if not in list so it's always selectable
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
  // If empty/error: keep existing input+datalist, nothing more to do
}

// Step 4 — Review + save
function renderStep4() {
  const prov = providerById(wizard.provider);
  const sc = wizard.serverConfig;
  const cfgFile = sc ? sc.config_file : "~/.config/ghostclaw/ghostclaw.toml";

  const keyDisplay = wizard.apiKey
    ? (wizard.apiKey.length > 8
       ? wizard.apiKey.slice(0, 4) + "…" + wizard.apiKey.slice(-4)
       : "set")
    : (sc && sc.api_key_masked ? sc.api_key_masked + " (unchanged)" : "—");

  const burlDisplay = wizard.baseUrl || prov.defaultBaseUrl;

  els.wizardBody.innerHTML = `
    <h2>Review &amp; Save</h2>
    <p class="wdesc">Double-check your configuration before saving.</p>
    <table class="review-table">
      <tr><td>Provider</td><td>${escHtml(prov.name)} <span style="color:var(--muted);font-size:11px">(${escHtml(prov.id)})</span></td></tr>
      <tr><td>Model</td><td>${escHtml(wizard.model || prov.defaultModel)}</td></tr>
      ${prov.needsKey ? `<tr><td>API Key</td><td>${escHtml(keyDisplay)}</td></tr>` : ""}
      <tr><td>Base URL</td><td>${escHtml(burlDisplay)}</td></tr>
    </table>
    <div class="config-file-note">
      Configuration will be written to:<br>
      <code>${escHtml(cfgFile)}</code>
    </div>
    <div id="wiz-save-error" class="wizard-error" style="display:none"></div>
  `;
}

function renderWizardSuccess(cfgFile) {
  els.wizardBody.innerHTML = `
    <div class="wizard-success">
      <div class="success-icon">✅</div>
      <h3>Configuration Saved!</h3>
      <p>
        Written to:<br>
        <code>${escHtml(cfgFile || "")}</code>
      </p>
      <p style="margin-top:12px;color:var(--ok)">
        Configuration applied — you can start chatting now.
      </p>
    </div>
  `;
  // Update footer to just a close button
  els.wbtnBack.style.display = "none";
  els.wbtnNext.style.display = "none";
  els.wbtnSave.style.display = "none";
  els.wbtnSkip.style.display = "";
  els.wbtnSkip.textContent   = "Close";
}

// Validate current step; return error message or ""
function validateStep() {
  const prov = providerById(wizard.provider);
  switch (wizard.step) {
    case 1:
      if (!wizard.provider) return "Please select a provider.";
      break;
    case 2:
      if (prov.needsKey) {
        if (wizard.apiKey && /^https?:\/\//i.test(wizard.apiKey)) {
          return "API Key looks like a URL. Paste the key value, not the endpoint URL.";
        }
        // Key is required only if the server doesn't already have one set
        const alreadySet = wizard.serverConfig && wizard.serverConfig.api_key_set;
        if (!wizard.apiKey && !alreadySet) {
          return `${prov.keyLabel} is required.`;
        }
      }
      break;
    case 3:
      if (!(wizard.model || prov.defaultModel)) return "Please enter a model name.";
      break;
  }
  return "";
}

function wizardNext() {
  const err = validateStep();
  if (err) { showWizardValidationError(err); return; }
  if (wizard.step === 1) {
    // Always reset baseUrl to the selected provider's default when leaving
    // step 1, so stale endpoints from previous providers don't carry over.
    wizard.baseUrl = providerById(wizard.provider).defaultBaseUrl || "";
  }
  if (wizard.step < wizard.totalSteps) {
    wizard.step++;
    renderWizardStep();
  }
}

function wizardBack() {
  if (wizard.step > 1) {
    wizard.step--;
    renderWizardStep();
  }
}

function showWizardValidationError(msg) {
  // Remove existing error if any
  const existing = els.wizardBody.querySelector(".wizard-error");
  if (existing) existing.remove();
  const el = document.createElement("div");
  el.className = "wizard-error";
  el.textContent = msg;
  els.wizardBody.appendChild(el);
}

function wizardSave() {
  const prov = providerById(wizard.provider);
  const model = wizard.model || prov.defaultModel;
  const baseUrl = (wizard.baseUrl || "").trim() || prov.defaultBaseUrl;

  const config = {
    provider: {
      name: wizard.provider,
      base_url: baseUrl,
    },
    agent: {
      model,
    },
  };

  // Only include api_key if user typed a new one
  if (prov.needsKey && wizard.apiKey) {
    config.provider.api_key = wizard.apiKey;
  }

  // Show save error area (in case of error response)
  const errEl = wizardEl("wiz-save-error");
  if (errEl) errEl.style.display = "none";

  els.wbtnSave.disabled = true;
  els.wbtnSave.textContent = "Saving…";
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
  scrollToBottom();
}

function finalizeAiMsg() {
  // Remove the AI bubble if it was created but received no content
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;
  state._toolBubbles = {};
  state._toolIndex   = 0;
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
  // If the current AI bubble has only whitespace, discard it — the AI jumped
  // straight into a tool call without producing visible text.
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  // Detach the current AI bubble from state so the next text chunk creates a
  // *new* bubble positioned below this tool bubble.  Without this, all rounds'
  // text accumulates in the first bubble (above every tool bubble), which
  // scrolls out of view and looks like the content disappeared.
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;

  const idx = state._toolIndex++;
  const wrapper = document.createElement("div");
  wrapper.className = "tool-bubble";
  wrapper.dataset.idx = idx;

  const header = document.createElement("div");
  header.className = "tool-header";
  header.innerHTML = `
    <span class="tool-arrow">▶</span>
    <span class="tool-name">→ ${escHtml(data.tool || "tool")}</span>
    <span class="tool-meta" style="color:var(--muted);font-size:11px;margin-left:auto"></span>
  `;
  header.addEventListener("click", () => wrapper.classList.toggle("open"));

  const body = document.createElement("div");
  body.className = "tool-body";
  body.innerHTML = `
    <div class="tool-section-label">INPUT</div>
    <pre class="tool-pre">${escHtml(prettyJson(data.input))}</pre>
    <div class="tool-section-label result-label" style="display:none">RESULT</div>
    <pre class="tool-pre result-pre" style="display:none"></pre>
  `;

  wrapper.appendChild(header);
  wrapper.appendChild(body);
  els.messages.appendChild(wrapper);
  state._toolBubbles["__last_" + data.tool] = wrapper;
  scrollToBottom();
}

function updateToolBubble(data) {
  const bubble = state._toolBubbles["__last_" + data.tool];
  if (!bubble) return;
  const resultLabel = bubble.querySelector(".result-label");
  const resultPre   = bubble.querySelector(".result-pre");
  if (resultLabel && resultPre) {
    resultLabel.style.display = "";
    resultPre.style.display   = "";
    resultPre.textContent = typeof data.result === "string"
      ? data.result
      : prettyJson(data.result);
    const meta = bubble.querySelector(".tool-meta");
    if (meta) meta.textContent = "✓";
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
      state.session_id = s.session_id;
      els.sessionLabel.textContent = `session: ${s.session_id}`;
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
  els.input.disabled = v;
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
  state.session_id = null;
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
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

  insertUserMsg(text);
  els.input.value = "";
  autoResize();
  setSending(true);

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

// Settings button
els.btnSettings.addEventListener("click", () => {
  // Re-fetch fresh config status before opening
  send({ type: "get_config_status" });
  // Open after a tick (server will push config_status which calls openWizard)
  // But open immediately with current data as fallback
  openWizard(true /* dismissible */);
});

// Wizard navigation
els.wbtnNext.addEventListener("click", wizardNext);
els.wbtnBack.addEventListener("click", wizardBack);
els.wbtnSave.addEventListener("click", wizardSave);
els.wbtnSkip.addEventListener("click", closeWizard);

// Close wizard on overlay background click (only if dismissible)
els.wizardOverlay.addEventListener("click", (ev) => {
  if (ev.target === els.wizardOverlay) {
    if (els.wbtnSkip.style.display !== "none") closeWizard();
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────

insertSystemMsg("Connecting to GhostClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";
connect();
