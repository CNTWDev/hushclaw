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
};

// ── Settings modal state ───────────────────────────────────────────────────

export const wizard = {
  tab: "model",
  dismissible: true,
  savedOnce: false,
  _pendingRefresh: false,
  provider: "anthropic-raw",
  apiKey: "",
  baseUrl: "",
  model: "claude-sonnet-4-6",
  maxTokens: 4096,
  maxToolRounds: 10,
  systemPrompt: "",
  costIn: 0.0,
  costOut: 0.0,
  toolsProfile: "",
  workspaceDir: "",
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
  serverConfig: null,
  open: false,
  saving: false,
  saveStatus: { text: "", type: "" },
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
  addingNew: false,
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
  memorySearch:      $("memory-search"),
  btnSearchMem:      $("btn-search-memories"),
  btnRefreshMem:     $("btn-refresh-memories"),
  btnRefreshSess:    $("btn-refresh-sessions"),
  btnRefreshAgents:  $("btn-refresh-agents"),
  btnAddAgent:       $("btn-add-agent"),
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
