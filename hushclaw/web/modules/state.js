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
  _tabToRestorePending: null,  // ← tab to restore after WebSocket connects
  _toolBubbles: {},
  _toolPendingByName: {},
  _toolIndex: 0,
  _aiMsgEl: null,
  _aiBubbleEl: null,
  _lastUserMsgEl: null,
  _streamingSessionId: null,
  _thinkingEl: null,
  _thinkingTimer: null,
  _thinkingStart: 0,
  _mentionActive: false,
  _mentionIndex: 0,
  _mentionItems: [],
  _firstSessionLoad: true,
  _activeSessionId: null,
  _attachments: [],
  _messageReferences: [],
  _uploadPending: new Map(),
  _sessionRunState: {}, // session_id -> {status, startedAt, lastMode}
  _pendingSessionStart: false,
  _profileFacts: {
    offset: 0,
    limit: 50,
    query: "",
    category: "",
    total: 0,
    hasMore: false,
  },
  // Workspace — null means "default" (no override).
  // Read from localStorage eagerly so the first list_sessions call (ws.onopen)
  // already carries the correct workspace before config_status arrives.
  activeWorkspace: (() => {
    try { return localStorage.getItem("hushclaw.ui.workspace") || null; } catch { return null; }
  })(),
  workspacesList: [],   // [{name, path, description}, ...]
  briefing: null,
  briefingDismissed: new Set(),
};

// ── Settings modal state ───────────────────────────────────────────────────

export const wizard = {
  tab: "model",
  theme: "vector",
  themeMode: "auto",
  dismissible: true,
  savedOnce: false,
  providerTestOk: false,
  _pendingRefresh: false,
  provider: "anthropic-raw",
  apiKey: "",
  baseUrl: "",
  providerTimeout: 360,
  model: "claude-sonnet-4-6",
  maxTokens: 4096,
  maxToolRounds: 40,
  systemPrompt: "",
  systemPromptDefault: true,
  systemPromptTouched: false,
  costIn: 0.0,
  costOut: 0.0,
  toolsProfile: "",
  workspaceDir: "",
  historyBudget: 140000,
  compactThreshold: 0.9,
  compactKeepTurns: 16,
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
  /** Free-form API keys for skills/integrations. Keyed by config name (e.g. scrape_creators). */
  apiKeys: {},
  /** Workspace registry — mirrors config.workspaces.list */
  workspacesList: [],
};

export const updateState = {
  checking: false,
  upgrading: false,
  lastStatus: null,
  // Set to true just before run_update is sent so that the WebSocket
  // disconnect caused by install.sh killing the server is recognised as
  // expected and treated as an upgrade success rather than an error.
  expectingDisconnect: false,
  // Set to true while waiting for a post-reconnect version check to confirm
  // the upgrade actually changed the version number.
  verifyingUpgrade: false,
  // The version string recorded immediately before the upgrade was triggered;
  // compared against the post-reconnect check_update response to detect a
  // false-positive success.
  versionBeforeUpgrade: "",
};

// ── Connector / integration state ──────────────────────────────────────────

export const connectors = {
  telegram: {
    enabled: false, bot_token: "", bot_token_set: false,
    agent: "default", workspace: "",
    allowlist: "", group_allowlist: "",
    group_policy: "allowlist", require_mention: false, stream: true, markdown: true,
  },
  feishu: {
    enabled: false, app_id: "",
    app_secret: "", app_secret_set: false,
    encrypt_key: "", encrypt_key_set: false,
    verification_token: "", verification_token_set: false,
    agent: "default", workspace: "", allowlist: "", stream: false, markdown: true,
  },
  discord: {
    enabled: false, bot_token: "", bot_token_set: false,
    agent: "default", workspace: "", allowlist: "", guild_allowlist: "",
    require_mention: true, stream: true, markdown: true,
  },
  slack: {
    enabled: false, bot_token: "", bot_token_set: false,
    app_token: "", app_token_set: false,
    agent: "default", workspace: "", allowlist: "", stream: true, markdown: true,
  },
  dingtalk: {
    enabled: false, client_id: "",
    client_secret: "", client_secret_set: false,
    agent: "default", workspace: "", allowlist: "", stream: true, markdown: true,
  },
  wecom: {
    enabled: false, corp_id: "",
    corp_secret: "", corp_secret_set: false,
    agent_id: 0, token: "", token_set: false,
    agent: "default", workspace: "", allowlist: "", markdown: true,
  },
};

export const appConnectors = {
  broker_base_url: "https://bus-ie.aibotplatform.com/hushclaw/app-connectors/oauth",
  github: {
    enabled: false,
    auth_mode: "managed",
    auth_type: "pat",
    client_id: "",
    client_id_ref: "app_connectors.github.client_id",
    client_id_set: false,
    client_secret: "",
    client_secret_ref: "app_connectors.github.client_secret",
    client_secret_set: false,
    token: "",
    token_ref: "app_connectors.github.token",
    token_set: false,
    default_repo: "",
    allow_actions: false,
  },
  google_workspace: {
    enabled: false,
    auth_mode: "managed",
    auth_type: "oauth",
    client_id: "",
    client_id_ref: "app_connectors.google_workspace.client_id",
    client_id_set: false,
    client_secret: "",
    client_secret_ref: "app_connectors.google_workspace.client_secret",
    client_secret_set: false,
    access_token: "",
    access_token_ref: "app_connectors.google_workspace.access_token",
    access_token_set: false,
    refresh_token: "",
    refresh_token_ref: "app_connectors.google_workspace.refresh_token",
    refresh_token_set: false,
    scopes: [
      "https://www.googleapis.com/auth/drive.readonly",
      "https://www.googleapis.com/auth/gmail.readonly",
      "https://www.googleapis.com/auth/calendar.readonly",
    ],
    allow_actions: false,
  },
  notion: {
    enabled: false,
    auth_mode: "managed",
    auth_type: "internal_token",
    client_id: "",
    client_id_ref: "app_connectors.notion.client_id",
    client_id_set: false,
    client_secret: "",
    client_secret_ref: "app_connectors.notion.client_secret",
    client_secret_set: false,
    token: "",
    token_ref: "app_connectors.notion.token",
    token_set: false,
    workspace_name: "",
    allow_actions: false,
  },
  jira: {
    enabled: false,
    auth_mode: "managed",
    auth_type: "api_token",
    site_url: "",
    email: "",
    client_id: "",
    client_id_ref: "app_connectors.jira.client_id",
    client_id_set: false,
    client_secret: "",
    client_secret_ref: "app_connectors.jira.client_secret",
    client_secret_set: false,
    token: "",
    token_ref: "app_connectors.jira.token",
    token_set: false,
    access_token: "",
    access_token_ref: "app_connectors.jira.access_token",
    access_token_set: false,
    refresh_token: "",
    refresh_token_ref: "app_connectors.jira.refresh_token",
    refresh_token_set: false,
    cloud_id: "",
    scopes: ["read:jira-work", "read:jira-user", "offline_access"],
    allow_actions: false,
  },
};

export const appConnectorsPanel = {
  selected: "github",
  saveStatus: "",
  saveStatusType: "",
  testStatus: "",
  testStatusType: "",
};

export const browser = {
  enabled: true,
  headless: true,
  timeout: 30,
  playwright_installed: false,
  use_user_chrome: false,
  remote_debugging_url: "",
};

export function _defaultEmailAccount() {
  return { label: "", enabled: false, imap_host: "", imap_port: 993,
           smtp_host: "", smtp_port: 587, username: "",
           password: "", password_set: false, mailbox: "INBOX" };
}
export function _defaultCalendarAccount() {
  return { label: "", enabled: false, url: "", username: "",
           password: "", password_set: false, calendar_name: "",
           timezone: "" };
}

export const emailAccounts = [_defaultEmailAccount()];
export const calendarAccounts = [_defaultCalendarAccount()];
export let currentEmailTab = 0;
export let currentCalendarTab = 0;
export function setCurrentEmailTab(i) { currentEmailTab = i; }
export function setCurrentCalendarTab(i) { currentCalendarTab = i; }

// Backward-compat aliases (used by older imports that reference emailCfg/calendarCfg)
export const emailCfg = emailAccounts[0];
export const calendarCfg = calendarAccounts[0];

export const skills = {
  installed: [],
  catalog: [],
  skillDir: "",
  userSkillDir: "",
  configured: false,
  total: 0,
  counts: {},
  health: null,
  detail: null,
  query: "",
  scope: "all",
  status: "all",
  sort: "name",
  offset: 0,
  limit: 80,
  repos: [],
  categories: [],
  activeCategory: "All",
  reposLoading: false,
  reposError: "",
  installing: new Set(),
};

export const learning = {
  profileSnapshot: {},
  profileText: "",
  reflections: [],
  skillOutcomes: [],
};

export const agentsState = {
  items: [],
  runtimeStatusByAgent: {},
  agentDetail: null,
  query: "",
  filter: "all",
  testingAgent: null,
  runningTestAgent: null,
  testDrafts: {},
  testResults: {},
  editingAgent: null,
  addingNew: false,
};

export const tasksState = {
  todos: [],
  scheduled: [],
  work: [],
  workStatus: "",
  addingTodo: false,
  todoPriority: false,
  addingSched: false,
  addingWork: false,
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

export const els = {
  panelChat:         $("panel-chat"),
  chatArea:          $("chat-area"),
  agentSelect:       $("agent-select"),
  messages:          $("messages"),
  workspaceBriefing: $("workspace-briefing"),
  input:             $("input"),
  btnSend:           $("btn-send"),
  btnStop:           $("btn-stop"),
  btnAttach:         $("btn-attach"),
  fileInput:         $("file-input"),
  attachmentChips:   $("attachment-chips"),
  sessionRuntimeBar: $("session-runtime-bar"),
  sessionRuntimeDot: $("session-runtime-dot"),
  sessionRuntimeLabel: $("session-runtime-label"),
  sessionRuntimeSummary: $("session-runtime-summary"),
  btnHandoverDone:   $("btn-handover-done"),
  handoverBanner:    $("handover-banner"),
  handoverMsg:       $("handover-msg"),
  btnNew:            $("btn-new-session"),
  btnExportPdf:      $("btn-export-pdf"),
  sessionLabel:      $("session-label"),
  connStatus:        $("conn-status"),
  tokenStats:        $("token-stats"),
  sessionsList:      $("sessions-list"),
  sessionSearch:     $("session-search"),
  memoriesList:      $("memories-list"),
  memoriesCount:     $("memories-count"),
  memorySearch:      $("memory-search"),
  btnSearchMem:      $("btn-search-memories"),
  btnRefreshMem:     $("btn-refresh-memories"),
  btnCompactMem:     $("btn-compact-memories"),
  btnRefreshSess:    $("btn-refresh-sessions"),
  btnSearchSess:     $("btn-search-sessions"),
  btnClearSessSearch:$("btn-clear-session-search"),
  btnToggleSess:     $("btn-toggle-sessions"),
  btnToggleSessInline: $("btn-toggle-sessions-inline"),
  btnRefreshAgents:  $("btn-refresh-agents"),
  btnAddAgent:       $("btn-add-agent"),
  skillsContent:     $("skills-content"),
  skillDirBadge:     $("skill-dir-badge"),
  btnRefreshSkills:  $("btn-refresh-skills"),
  memoriesOverview:  $("memories-overview"),
  memoriesProfile:   $("memories-profile"),
  memoriesBeliefs:   $("memories-beliefs"),
  memoriesOpinions:  $("memories-opinions"),
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

/** Monotonic id for list_memories — stale WS responses are dropped so they cannot undo a delete. */
export let memoriesListRequestGen = 0;

export function sendListMemories(query = "", limit = 50, includeAuto = false, offset = 0, memoryKinds = null) {
  memoriesListRequestGen += 1;
  const msg = {
    type: "list_memories",
    query: String(query || "").trim(),
    limit,
    include_auto: includeAuto,
    offset,
    request_id: memoriesListRequestGen,
  };
  if (Array.isArray(memoryKinds) && memoryKinds.length) msg.memory_kinds = memoryKinds;
  if (state.activeWorkspace) msg.workspace = state.activeWorkspace;
  send(msg);
}

export function sendListProfileFacts({ offset = 0, limit = 50, query = "", category = "" } = {}) {
  const normalizedOffset = Math.max(0, Math.trunc(Number(offset) || 0));
  const normalizedLimit = Math.max(1, Math.trunc(Number(limit) || 50));
  state._profileFacts.offset = normalizedOffset;
  state._profileFacts.limit = normalizedLimit;
  state._profileFacts.query = String(query || "");
  state._profileFacts.category = String(category || "");
  send({
    type: "list_profile_facts",
    offset: normalizedOffset,
    limit: normalizedLimit,
    query: state._profileFacts.query,
    category: state._profileFacts.category,
  });
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
  syncComposerState();
}

export function syncComposerState() {
  const sid = getCurrentSessionId();
  const runtime = sid ? getSessionRuntime(sid) : null;
  const currentRunning = Boolean(sid && ["queued", "running"].includes(runtime?.status || getSessionStatus(sid)));
  const pendingStart = Boolean(state._pendingSessionStart && !sid);
  const wsOpen = Boolean(state.ws && state.ws.readyState === WebSocket.OPEN);
  const busy = currentRunning || pendingStart;
  state.sending = busy;
  els.btnSend.disabled = busy || !wsOpen;
  els.btnSend.textContent = busy ? "⠸" : "↑";
  els.input.disabled = busy;
  els.btnStop.classList.toggle("hidden", !currentRunning);
  updateCurrentSessionRuntimeBar();
}

export function getCurrentSessionId() {
  return state.session_id || state._activeSessionId || "";
}

export function setCurrentSessionId(sessionId) {
  const sid = sessionId || null;
  if (sid) state._pendingSessionStart = false;
  state.session_id = sid;
  state._activeSessionId = sid;
  if (els.sessionLabel) {
    const idEl = document.getElementById("session-id-text");
    if (idEl) {
      idEl.textContent = sid || "—";
    } else {
      els.sessionLabel.textContent = sid ? `session: ${sid}` : "session: —";
    }
  }
  syncComposerState();
}

export function clearCurrentSessionId() {
  setCurrentSessionId(null);
}

export function setSessionStatus(sessionId, status, reason = "", mode = "thinking", ts = Date.now()) {
  if (!sessionId) return;
  const prev = state._sessionRunState[sessionId];
  state._sessionRunState[sessionId] = {
    ...prev,
    status,
    reason,
    ts,
    startedAt: prev?.startedAt || Date.now(),
    lastMode: mode,
    phase: mode,
    summary: prev?.summary || "",
    updatedAt: ts,
  };
}

export function setSessionRuntime(sessionId, runtime = {}) {
  if (!sessionId) return;
  const prev = state._sessionRunState[sessionId] || {};
  const status = runtime.status || prev.status || "idle";
  const phase = runtime.phase || prev.phase || prev.lastMode || status;
  state._sessionRunState[sessionId] = {
    ...prev,
    ...runtime,
    status,
    phase,
    lastMode: phase,
    summary: runtime.summary || prev.summary || "",
    reason: runtime.reason || prev.reason || "",
    ts: runtime.updated_at || runtime.ts || Date.now(),
    updatedAt: runtime.updated_at || runtime.ts || Date.now(),
    startedAt: runtime.started_at || prev.startedAt || Date.now(),
  };
  if (sessionId === getCurrentSessionId()) updateCurrentSessionRuntimeBar();
}

export function markSessionRunning(sessionId, mode = "thinking", resetTimer = false) {
  if (!sessionId) return;
  const prev = state._sessionRunState[sessionId];
  state._sessionRunState[sessionId] = {
    ...prev,
    status: "running",
    reason: "local_infer",
    ts: Date.now(),
    startedAt: resetTimer ? Date.now() : (prev?.startedAt || Date.now()),
    lastMode: mode,
    phase: mode,
    summary: prev?.summary || "",
  };
}

export function markSessionIdle(sessionId) {
  setSessionStatus(sessionId, "idle", "local_infer", "idle");
}

export function getSessionStatus(sessionId) {
  if (!sessionId) return "idle";
  return state._sessionRunState[sessionId]?.status || "idle";
}

export function getSessionRuntime(sessionId) {
  if (!sessionId) return null;
  return state._sessionRunState[sessionId] || null;
}

export function isSessionRunning(sessionId) {
  return ["queued", "running"].includes(getSessionStatus(sessionId));
}

export function sessionRuntimeLabel(runtime = {}) {
  const status = runtime.status || "idle";
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "waiting_user") return "Waiting";
  if (status === "completed") return "Done";
  if (status === "failed") return "Failed";
  if (status === "stopped") return "Stopped";
  if (status === "offline" || status === "stale") return "Syncing";
  return "";
}

export function sessionRuntimeSummary(runtime = {}) {
  const status = runtime.status || "idle";
  const summary = (runtime.summary || "").trim();
  if (summary && status !== "idle") return summary;
  if (status === "queued") return "Queued";
  if (status === "running") return "Working";
  if (status === "waiting_user") return "Waiting for you";
  if (status === "completed") return "Completed";
  if (status === "failed") return runtime.last_error || "Failed";
  if (status === "stopped") return "Stopped";
  if (status === "offline" || status === "stale") return "Syncing";
  return "";
}

export function updateCurrentSessionRuntimeBar() {
  const bar = els.sessionRuntimeBar;
  if (!bar) return;
  const sid = getCurrentSessionId();
  const runtime = sid ? getSessionRuntime(sid) : null;
  const status = runtime?.status || "idle";
  const visible = Boolean(runtime && status !== "idle");
  bar.classList.toggle("hidden", !visible);
  if (!visible) return;
  bar.dataset.status = status;
  if (els.sessionRuntimeLabel) els.sessionRuntimeLabel.textContent = sessionRuntimeLabel(runtime);
  if (els.sessionRuntimeSummary) els.sessionRuntimeSummary.textContent = sessionRuntimeSummary(runtime);
}

export function debugUiLifecycle(event, extra = {}) {
  try {
    if (localStorage.getItem("hushclaw.debug.ui") !== "1") return;
  } catch {
    return;
  }
  console.debug("[hushclaw-ui]", event, extra);
}
