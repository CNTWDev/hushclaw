/**
 * state.js — Shared state, DOM refs, and stateless utility functions.
 * Imported by every other module. Has no imports itself.
 */

export const SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];

// ── Core application state ─────────────────────────────────────────────────

export const state = {
  ws: null,
  session_id: null,
  agent: "default",
  agents: [],
  tab: "chat",
  inTokens: 0,
  outTokens: 0,
  sending: false,
  _reconnectDelay: 1000,
  _reconnectTimer: null,
  _reconnectCountdownTimer: null,
  // true until the very first successful WS connection — drives startup overlay
  _isInitialConnect: true,
  _reconnectAttempts: 0,
  _toolBubbles: {},
  _toolPendingByName: {},
  _toolIndex: 0,
  _aiMsgEl: null,
  _aiBubbleEl: null,
  _thinkingEl: null,
  _thinkingTimer: null,
  _thinkingStart: 0,
  _mentionActive: false,
  _mentionIndex: 0,
  _mentionItems: [],
  _firstSessionLoad: true,
  _activeSessionId: null,
  _attachments: [],
  _uploadPending: new Map(),
  _sessionRunState: {}, // session_id -> {status, startedAt, lastMode}
};

// ── Settings modal state ───────────────────────────────────────────────────

export const wizard = {
  tab: "model",
  theme: "slate",
  themeMode: "auto",
  dismissible: true,
  savedOnce: false,
  _pendingRefresh: false,
  provider: "anthropic-raw",
  apiKey: "",
  baseUrl: "",
  model: "claude-sonnet-4-6",
  maxTokens: 4096,
  maxToolRounds: 40,
  systemPrompt: "",
  costIn: 0.0,
  costOut: 0.0,
  toolsProfile: "",
  workspaceDir: "",
  historyBudget: 80000,
  compactThreshold: 0.9,
  compactKeepTurns: 6,
  compactStrategy: "lossless",
  memoryMinScore: 0.2,
  memoryMaxTokens: 1200,
  autoExtract: true,
  memoryDecayRate: 0.0,
  retrievalTemperature: 0.0,
  serendipityBudget: 0.0,
  serverConfig: null,
  open: false,
  saving: false,
  saveStatus: { text: "", type: "" },
  updateAutoCheckEnabled: true,
  updateCheckIntervalHours: 24,
  updateChannel: "stable",
  updateCurrentVersion: "",
  updateLatestVersion: "",
  updateAvailable: false,
  updateReleaseUrl: "",
  updateLastCheckedAt: 0,
};

export const updateState = {
  checking: false,
  upgrading: false,
  lastStatus: null,
  // Set to true just before run_update is sent so that the WebSocket
  // disconnect caused by install.sh killing the server is recognised as
  // expected and treated as an upgrade success rather than an error.
  expectingDisconnect: false,
};

// ── Connector / integration state ──────────────────────────────────────────

export const connectors = {
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

export const browser = {
  enabled: true,
  headless: true,
  timeout: 30,
  playwright_installed: false,
  use_user_chrome: false,
  remote_debugging_url: "",
};

export const emailCfg = {
  enabled: false, imap_host: "", imap_port: 993,
  smtp_host: "", smtp_port: 587, username: "",
  password: "", password_set: false, mailbox: "INBOX",
};

export const calendarCfg = {
  enabled: false, url: "", username: "",
  password: "", password_set: false, calendar_name: "",
};

export const skills = {
  installed: [],
  skillDir: "",
  configured: false,
  repos: [],
  categories: [],
  activeCategory: "All",
  reposLoading: false,
  reposError: "",
  installing: new Set(),
};

export const agentsState = {
  items: [],
  expandedAgent: null,
  agentDetail: null,
  editingAgent: null,
  quickReportAgent: null,
  addingNew: false,
  collapsedChildren: {},
};

export const tasksState = {
  todos: [],
  scheduled: [],
  addingTodo: false,
  todoPriority: false,
  addingSched: false,
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

export const els = {
  panelChat:         $("panel-chat"),
  chatArea:          $("chat-area"),
  agentSelect:       $("agent-select"),
  messages:          $("messages"),
  input:             $("input"),
  btnSend:           $("btn-send"),
  btnStop:           $("btn-stop"),
  btnAttach:         $("btn-attach"),
  fileInput:         $("file-input"),
  attachmentChips:   $("attachment-chips"),
  btnHandoverDone:   $("btn-handover-done"),
  handoverBanner:    $("handover-banner"),
  handoverMsg:       $("handover-msg"),
  btnNew:            $("btn-new-session"),
  btnSettings:       $("btn-settings"),
  sessionLabel:      $("session-label"),
  connStatus:        $("conn-status"),
  tokenStats:        $("token-stats"),
  sessionsList:      $("sessions-list"),
  memoriesList:      $("memories-list"),
  memoriesCount:     $("memories-count"),
  memorySearch:      $("memory-search"),
  btnSearchMem:      $("btn-search-memories"),
  btnRefreshMem:     $("btn-refresh-memories"),
  btnRefreshSess:    $("btn-refresh-sessions"),
  btnToggleSess:     $("btn-toggle-sessions"),
  btnToggleSessInline: $("btn-toggle-sessions-inline"),
  btnRefreshAgents:  $("btn-refresh-agents"),
  btnAddAgent:       $("btn-add-agent"),
  btnRunHierarchy:   $("btn-run-hierarchy"),
  skillsContent:     $("skills-content"),
  skillDirBadge:     $("skill-dir-badge"),
  btnRefreshSkills:  $("btn-refresh-skills"),
  wizardOverlay:     $("wizard-overlay"),
  wizardBody:        $("wizard-body"),
  settingsTabs:      $("settings-tabs"),
  wbtnClose:         $("wbtn-close"),
  wbtnSave:          $("wbtn-save"),
  wstatus:           $("wstatus"),
};

// ── Utility functions (no outbound imports) ────────────────────────────────

export function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

export function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function prettyJson(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

export function showToast(msg, level = "info") {
  const el = document.createElement("div");
  el.className = `toast toast-${level}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

export function showSkillToast(msg, kind) {
  const el = document.createElement("div");
  el.className = `skill-toast ${kind}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

export function setConnStatus(status) {
  els.connStatus.className = `dot ${status}`;
  els.connStatus.title = status.charAt(0).toUpperCase() + status.slice(1);
}

export function updateTokenStats() {
  if (state.inTokens || state.outTokens) {
    els.tokenStats.textContent =
      `In: ${state.inTokens.toLocaleString()}  Out: ${state.outTokens.toLocaleString()}`;
  }
}

export function setSending(v) {
  state.sending = v;
  els.btnSend.disabled = v || !state.ws || state.ws.readyState !== WebSocket.OPEN;
  els.btnSend.textContent = v ? "⠸" : "↑";
  els.input.disabled = v;
  els.btnStop.classList.toggle("hidden", !v);
}

export function getCurrentSessionId() {
  return state.session_id || state._activeSessionId || "";
}

export function setCurrentSessionId(sessionId) {
  const sid = sessionId || null;
  state.session_id = sid;
  state._activeSessionId = sid;
  if (els.sessionLabel) {
    els.sessionLabel.textContent = sid ? `session: ${sid}` : "session: —";
  }
}

export function clearCurrentSessionId() {
  setCurrentSessionId(null);
}

export function setSessionStatus(sessionId, status, reason = "", mode = "thinking", ts = Date.now()) {
  if (!sessionId) return;
  const prev = state._sessionRunState[sessionId];
  state._sessionRunState[sessionId] = {
    status,
    reason,
    ts,
    startedAt: prev?.startedAt || Date.now(),
    lastMode: mode,
  };
}

export function markSessionRunning(sessionId, mode = "thinking", resetTimer = false) {
  if (!sessionId) return;
  const prev = state._sessionRunState[sessionId];
  state._sessionRunState[sessionId] = {
    status: "running",
    reason: "local_infer",
    ts: Date.now(),
    startedAt: resetTimer ? Date.now() : (prev?.startedAt || Date.now()),
    lastMode: mode,
  };
}

export function markSessionIdle(sessionId) {
  setSessionStatus(sessionId, "idle", "local_infer", "idle");
}

export function getSessionStatus(sessionId) {
  if (!sessionId) return "idle";
  return state._sessionRunState[sessionId]?.status || "idle";
}

export function isSessionRunning(sessionId) {
  return getSessionStatus(sessionId) === "running";
}

export function debugUiLifecycle(event, extra = {}) {
  try {
    if (localStorage.getItem("hushclaw.debug.ui") !== "1") return;
  } catch {
    return;
  }
  console.debug("[hushclaw-ui]", event, extra);
}
