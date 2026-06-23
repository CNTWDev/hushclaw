/**
 * state.js — Shared state, DOM refs, and stateless utility functions.
 * Imported by every other module. Has no imports itself.
 */

export const SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
const _COMPOSER_DRAFTS_KEY = "hushclaw.ui.composer-drafts";
const _WORKBENCH_PREVIEW_KEY = "hushclaw.ui.workbench-preview";
const _WORKBENCH_RUNTIME_UI_KEY = "hushclaw.ui.workbench-runtime";

function _loadComposerDrafts() {
  try {
    const raw = localStorage.getItem(_COMPOSER_DRAFTS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function _loadWorkbenchPreviewState() {
  try {
    const raw = localStorage.getItem(_WORKBENCH_PREVIEW_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function _loadWorkbenchRuntimeUiState() {
  try {
    const raw = localStorage.getItem(_WORKBENCH_RUNTIME_UI_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

// ── Core application state ─────────────────────────────────────────────────

export const state = {
  ws: null,
  session_id: null,
  currentSessionTitle: "",
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
  _sessionTitlesById: {},
  _attachments: [],
  _messageReferences: [],
  _uploadPending: new Map(),
  _sessionRunState: {}, // session_id -> {status, startedAt, lastMode}
  _pendingSessionStart: false,
  _durableSendQueue: [],
  _sessionRuntimeFeed: {},
  _sessionRuntimeLogOpen: true,
  _composerDrafts: _loadComposerDrafts(),
  _workbenchActivity: [],
  _workbenchPreviewBySession: _loadWorkbenchPreviewState(),
  _workbenchRuntimeUiBySession: _loadWorkbenchRuntimeUiState(),
  _runtimeCardOpen: {},
  _runtimeFocusedRunId: "",
  _runtimeMonitorHidden: false,
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
  apiKeyRegistry: [],
  apiKeyDrafts: {},
  apiKeyClears: {},
  /** Workspace registry — mirrors config.workspaces.list */
  workspacesList: [],
};

export const updateState = {
  checking: false,
  prepareChecking: false,
  upgrading: false,
  lastStatus: null,
  prepareResult: null,
  pendingForceWhenBusy: false,
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
    group_policy: "allowlist", require_mention: false, stream: true, render_mode: "telegram_html",
  },
  feishu: {
    enabled: false, app_id: "",
    app_secret: "", app_secret_set: false,
    encrypt_key: "", encrypt_key_set: false,
    verification_token: "", verification_token_set: false,
    agent: "default", workspace: "", allowlist: "", stream: false, render_mode: "feishu_post",
  },
  discord: {
    enabled: false, bot_token: "", bot_token_set: false,
    agent: "default", workspace: "", allowlist: "", guild_allowlist: "",
    require_mention: true, stream: true, render_mode: "discord_markdown",
  },
  slack: {
    enabled: false, bot_token: "", bot_token_set: false,
    app_token: "", app_token_set: false,
    agent: "default", workspace: "", allowlist: "", stream: true, render_mode: "slack_mrkdwn",
  },
  dingtalk: {
    enabled: false, client_id: "",
    client_secret: "", client_secret_set: false,
    agent: "default", workspace: "", allowlist: "", stream: true, render_mode: "sample_markdown",
  },
  wecom: {
    enabled: false, corp_id: "",
    corp_secret: "", corp_secret_set: false,
    agent_id: 0, token: "", token_set: false,
    agent: "default", workspace: "", allowlist: "", stream: false, render_mode: "wecom_markdown",
  },
  whatsapp: {
    enabled: false, account_sid: "",
    auth_token: "", auth_token_set: false,
    from_number: "",
    agent: "default", workspace: "", allowlist: "", stream: false, render_mode: "plain",
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
  reddit: {
    enabled: false,
    auth_mode: "custom",
    auth_type: "oauth",
    client_id: "",
    client_id_ref: "app_connectors.reddit.client_id",
    client_id_set: false,
    client_secret: "",
    client_secret_ref: "app_connectors.reddit.client_secret",
    client_secret_set: false,
    access_token: "",
    access_token_ref: "app_connectors.reddit.access_token",
    access_token_set: false,
    refresh_token: "",
    refresh_token_ref: "app_connectors.reddit.refresh_token",
    refresh_token_set: false,
    user_agent: "HushClaw-AppConnector/1.0",
    default_subreddit: "",
    allow_actions: false,
  },
  x: {
    enabled: false,
    auth_mode: "custom",
    auth_type: "app_keys",
    consumer_key: "",
    consumer_key_ref: "app_connectors.x.consumer_key",
    consumer_key_set: false,
    consumer_secret: "",
    consumer_secret_ref: "app_connectors.x.consumer_secret",
    consumer_secret_set: false,
    oauth_client_id: "",
    oauth_client_id_ref: "app_connectors.x.oauth_client_id",
    oauth_client_id_set: false,
    oauth_client_secret: "",
    oauth_client_secret_ref: "app_connectors.x.oauth_client_secret",
    oauth_client_secret_set: false,
    bearer_token: "",
    bearer_token_ref: "app_connectors.x.bearer_token",
    bearer_token_set: false,
    access_token: "",
    access_token_ref: "app_connectors.x.access_token",
    access_token_set: false,
    refresh_token: "",
    refresh_token_ref: "app_connectors.x.refresh_token",
    refresh_token_set: false,
    scopes: ["tweet.read", "tweet.write", "users.read", "offline.access"],
    stream_enabled: false,
    stream_rules: [],
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

export const connectionsView = {
  items: [],
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
  sourceInspection: null,
  inspectingSource: false,
  installScope: "user",
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
  todoLimit: 30,
  todoOffset: 0,
  todosHasMore: false,
  scheduled: [],
  work: [],
  insights: [],
  insightView: "curated",
  insightLimit: 30,
  insightOffset: 0,
  insightsHasMore: false,
  insightCleanupPreview: null,
  workStatus: "",
  addingTodo: false,
  addingInsight: false,
  todoPriority: false,
  addingSched: false,
  addingWork: false,
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

export const els = {
  panelChat:         $("panel-chat"),
  chatArea:          $("chat-area"),
  chatWorkspace:     $("chat-workspace"),
  chatMainColumn:    $("chat-main-column"),
  chatWorkbench:     $("chat-workbench"),
  runtimeMonitor:    $("runtime-monitor"),
  agentSelect:       $("agent-select"),
  messages:          $("messages"),
  chatStatsStrip:    $("chat-stats-strip"),
  chatContextMeta:   $("chat-context-meta"),
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
  sessionRuntimeBadge: $("session-runtime-badge"),
  sessionRuntimeToggle: $("session-runtime-toggle"),
  sessionRuntimeMeta: $("session-runtime-meta"),
  sessionRuntimeStack: $("session-runtime-stack"),
  sessionRuntimeLog: $("session-runtime-log"),
  workbenchFiles:    $("files-sidebar"),
  workbenchPreview:  $("workbench-preview"),
  workbenchPreviewTitle: $("workbench-preview-title"),
  workbenchPreviewMeta: $("workbench-preview-meta"),
  workbenchPreviewEmpty: $("workbench-preview-empty"),
  workbenchPreviewBody: $("workbench-preview-body"),
  workbenchPreviewPin: $("workbench-preview-pin"),
  workbenchPreviewClose: $("workbench-preview-close"),
  workbenchActivity: $("workbench-activity"),
  workbenchAttentionStrip: $("workbench-attention-strip"),
  workbenchActivityBody: $("workbench-activity-body"),
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
  btnToggleRuntimeInline: $("btn-toggle-runtime-inline"),
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

const _DURABLE_WS_TYPES = new Set(["chat", "pipeline", "orchestrate", "broadcast_mention"]);

function _isDurableMessage(obj) {
  const type = String(obj?.type || "").trim();
  return _DURABLE_WS_TYPES.has(type);
}

function _durableMessageKey(obj) {
  const clientTurnId = String(obj?.client_turn_id || "").trim();
  if (clientTurnId) return `turn:${clientTurnId}`;
  const type = String(obj?.type || "").trim();
  return type ? `type:${type}:${state._durableSendQueue.length}` : "";
}

function _dispatchDurableLifecycle(name, detail = {}) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

function _queueDurableMessage(obj) {
  const payload = JSON.parse(JSON.stringify(obj || {}));
  const key = _durableMessageKey(payload);
  if (key.startsWith("turn:")) {
    const idx = state._durableSendQueue.findIndex((item) => _durableMessageKey(item) === key);
    if (idx >= 0) state._durableSendQueue[idx] = payload;
    else state._durableSendQueue.push(payload);
  } else {
    state._durableSendQueue.push(payload);
  }
  _dispatchDurableLifecycle("hc:durable-message-queued", {
    type: String(payload.type || ""),
    clientTurnId: String(payload.client_turn_id || ""),
    sessionId: String(payload.session_id || ""),
  });
}

function _markDurableDispatched(obj) {
  _dispatchDurableLifecycle("hc:durable-message-dispatched", {
    type: String(obj?.type || ""),
    clientTurnId: String(obj?.client_turn_id || ""),
    sessionId: String(obj?.session_id || ""),
  });
}

export function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
    if (_isDurableMessage(obj)) _markDurableDispatched(obj);
    return "sent";
  }
  if (_isDurableMessage(obj)) {
    _queueDurableMessage(obj);
    return "queued";
  }
  return "dropped";
}

export function flushPendingSendQueue() {
  if (!(state.ws && state.ws.readyState === WebSocket.OPEN) || !state._durableSendQueue.length) return 0;
  const batch = state._durableSendQueue.splice(0, state._durableSendQueue.length);
  for (const payload of batch) {
    if (!payload.session_id && !getCurrentSessionId()) {
      state._pendingSessionStart = true;
    }
    state.ws.send(JSON.stringify(payload));
    _markDurableDispatched(payload);
  }
  syncComposerState();
  return batch.length;
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

function _draftSlot(sessionId) {
  const sid = String(sessionId || "").trim();
  return sid || "__new__";
}

function _persistComposerDrafts() {
  try {
    localStorage.setItem(_COMPOSER_DRAFTS_KEY, JSON.stringify(state._composerDrafts || {}));
  } catch {
    // ignore storage errors
  }
}

function _persistWorkbenchPreviewState() {
  try {
    localStorage.setItem(_WORKBENCH_PREVIEW_KEY, JSON.stringify(state._workbenchPreviewBySession || {}));
  } catch {
    // ignore storage errors
  }
}

function _persistWorkbenchRuntimeUiState() {
  try {
    localStorage.setItem(_WORKBENCH_RUNTIME_UI_KEY, JSON.stringify(state._workbenchRuntimeUiBySession || {}));
  } catch {
    // ignore storage errors
  }
}

export function setComposerDraft(sessionId, text) {
  const slot = _draftSlot(sessionId);
  const value = String(text || "");
  if (value.trim()) state._composerDrafts[slot] = value;
  else delete state._composerDrafts[slot];
  _persistComposerDrafts();
}

export function saveCurrentComposerDraft() {
  if (!els.input) return;
  setComposerDraft(getCurrentSessionId(), els.input.value || "");
}

export function restoreComposerDraft(sessionId) {
  if (!els.input) return;
  const slot = _draftSlot(sessionId);
  const value = String(state._composerDrafts?.[slot] || "");
  els.input.value = value;
}

export function clearComposerDraft(sessionId) {
  setComposerDraft(sessionId, "");
}

export function getWorkbenchPreviewState(sessionId) {
  const slot = _draftSlot(sessionId);
  return state._workbenchPreviewBySession?.[slot] || null;
}

export function setWorkbenchPreviewState(sessionId, previewState) {
  const slot = _draftSlot(sessionId);
  if (previewState && typeof previewState === "object") {
    state._workbenchPreviewBySession[slot] = previewState;
  } else {
    delete state._workbenchPreviewBySession[slot];
  }
  _persistWorkbenchPreviewState();
}

function _runtimeUiSlot(sessionId) {
  return _draftSlot(sessionId);
}

function _loadRuntimeUiForSession(sessionId) {
  const snapshot = state._workbenchRuntimeUiBySession?.[_runtimeUiSlot(sessionId)] || {};
  state._sessionRuntimeLogOpen = snapshot.logOpen !== false;
  state._runtimeFocusedRunId = String(snapshot.focusedRunId || "").trim();
  state._runtimeCardOpen = snapshot.openCards && typeof snapshot.openCards === "object" ? { ...snapshot.openCards } : {};
  state._runtimeMonitorHidden = Boolean(snapshot.monitorHidden);
}

function _persistRuntimeUiForSession(sessionId) {
  const slot = _runtimeUiSlot(sessionId);
  state._workbenchRuntimeUiBySession[slot] = {
    logOpen: Boolean(state._sessionRuntimeLogOpen),
    focusedRunId: String(state._runtimeFocusedRunId || "").trim(),
    openCards: { ...(state._runtimeCardOpen || {}) },
    monitorHidden: Boolean(state._runtimeMonitorHidden),
  };
  _persistWorkbenchRuntimeUiState();
}

export function syncComposerState() {
  const sid = getCurrentSessionId();
  const runtime = sid ? getSessionRuntime(sid) : null;
  const currentRunning = Boolean(sid && ["queued", "running"].includes(runtime?.status || getSessionStatus(sid)));
  const pendingStart = Boolean(state._pendingSessionStart && !sid);
  const locked = pendingStart;
  state.sending = pendingStart;
  els.btnSend.disabled = locked;
  els.btnSend.textContent = pendingStart ? "⠸" : "↑";
  els.input.disabled = locked;
  els.btnStop.classList.toggle("hidden", !currentRunning);
  updateCurrentSessionRuntimeBar();
}

export function getCurrentSessionId() {
  return state.session_id || state._activeSessionId || "";
}

export function rememberSessionTitle(sessionId, title) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const nextTitle = String(title || "").trim();
  if (!nextTitle) {
    delete state._sessionTitlesById[sid];
  } else {
    state._sessionTitlesById[sid] = nextTitle;
  }
  if (getCurrentSessionId() === sid) {
    state.currentSessionTitle = nextTitle || `Session ${sid.slice(-12)}`;
  }
}

export function forgetSessionTitle(sessionId) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  delete state._sessionTitlesById[sid];
  if (getCurrentSessionId() === sid) {
    state.currentSessionTitle = "";
  }
}

export function getCurrentSessionTitle() {
  return state.currentSessionTitle || "";
}

export function setCurrentSessionId(sessionId) {
  const sid = sessionId || null;
  const prevSid = state.session_id || state._activeSessionId || null;
  if (els.input) saveCurrentComposerDraft();
  if (sid) state._pendingSessionStart = false;
  state.session_id = sid;
  state._activeSessionId = sid;
  state.currentSessionTitle = sid
    ? (state._sessionTitlesById[sid] || `Session ${String(sid).slice(-12)}`)
    : "";
  if (els.sessionLabel) {
    const idEl = document.getElementById("session-id-text");
    if (idEl) {
      idEl.textContent = sid || "—";
    } else {
      els.sessionLabel.textContent = sid ? `session: ${sid}` : "session: —";
    }
  }
  _loadRuntimeUiForSession(sid);
  if (els.input && prevSid !== sid) restoreComposerDraft(sid);
  document.dispatchEvent(new CustomEvent("hc:session-context-changed", { detail: { sessionId: sid || "" } }));
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

function _normalizeRuntimeChildRun(childRun = {}) {
  const activeStep = childRun.active_step || {};
  return {
    run_id: String(childRun.run_id || "").trim(),
    thread_id: String(childRun.thread_id || "").trim(),
    parent_run_id: String(childRun.parent_run_id || "").trim(),
    agent_name: String(childRun.agent_name || childRun.agent || "").trim(),
    trigger_type: String(childRun.trigger_type || "").trim(),
    run_kind: String(childRun.run_kind || "child").trim(),
    visibility: String(childRun.visibility || "background").trim(),
    state: String(childRun.state || "running").trim(),
    summary: String(childRun.summary || "").trim(),
    updated_at: Number(childRun.updated_at || childRun.ts || Date.now()),
    active_step: {
      step_id: String(activeStep.step_id || "").trim(),
      step_type: String(activeStep.step_type || "").trim(),
      state: String(activeStep.state || "").trim(),
      summary: String(activeStep.summary || "").trim(),
      meta: activeStep.meta && typeof activeStep.meta === "object" ? { ...activeStep.meta } : {},
    },
  };
}

export function noteSessionChildRun(sessionId, childRun = {}) {
  const sid = String(sessionId || "").trim();
  const runId = String(childRun.run_id || "").trim();
  if (!sid || !runId) return;
  const prev = state._sessionRunState[sid] || {};
  const childRuns = Array.isArray(prev.child_runs) ? [...prev.child_runs] : [];
  const normalized = _normalizeRuntimeChildRun(childRun);
  const idx = childRuns.findIndex((item) => String(item?.run_id || "").trim() === runId);
  if (idx >= 0) childRuns[idx] = { ...childRuns[idx], ...normalized };
  else childRuns.unshift(normalized);
  childRuns.sort((a, b) => Number(b?.updated_at || 0) - Number(a?.updated_at || 0));
  state._sessionRunState[sid] = {
    ...prev,
    child_runs: childRuns.slice(0, 8),
    updatedAt: Number(normalized.updated_at || prev.updatedAt || Date.now()),
  };
  if (sid === getCurrentSessionId()) updateCurrentSessionRuntimeBar();
}

function _runtimeFeed(sessionId) {
  const sid = String(sessionId || "").trim();
  if (!sid) return [];
  if (!state._sessionRuntimeFeed[sid]) state._sessionRuntimeFeed[sid] = [];
  return state._sessionRuntimeFeed[sid];
}

export function pushSessionRuntimeEvent(sessionId, event = {}) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const feed = _runtimeFeed(sid);
  feed.push({
    id: `${Date.now()}-${feed.length + 1}`,
    level: String(event.level || "info"),
    label: String(event.label || "").trim(),
    summary: String(event.summary || "").trim(),
    ts: Number(event.ts || Date.now()),
    scope: String(event.scope || "").trim(),
    state: String(event.state || "").trim(),
    child_run_id: String(event.child_run_id || "").trim(),
    run_id: String(event.run_id || "").trim(),
  });
  if (feed.length > 20) feed.splice(0, feed.length - 20);
  if (sid === getCurrentSessionId()) updateCurrentSessionRuntimeBar();
}

export function replaceSessionRuntimeFeed(sessionId, events = []) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const normalized = (Array.isArray(events) ? events : []).map((event, index) => ({
    id: String(event?.id || `${sid}-${Number(event?.ts || Date.now())}-${index}`),
    level: String(event?.level || "info"),
    label: String(event?.label || "").trim(),
    summary: String(event?.summary || "").trim(),
    ts: Number(event?.ts || Date.now()),
    scope: String(event?.scope || "").trim(),
    state: String(event?.state || "").trim(),
    child_run_id: String(event?.child_run_id || "").trim(),
    run_id: String(event?.run_id || "").trim(),
  }));
  state._sessionRuntimeFeed[sid] = normalized.slice(-20);
  if (sid === getCurrentSessionId()) updateCurrentSessionRuntimeBar();
}

export function clearSessionRuntimeFeed(sessionId) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  delete state._sessionRuntimeFeed[sid];
  state._runtimeCardOpen = {};
  if (sid === getCurrentSessionId()) updateCurrentSessionRuntimeBar();
}

export function setSessionRuntimeLogOpen(open) {
  state._sessionRuntimeLogOpen = !!open;
  _persistRuntimeUiForSession(getCurrentSessionId());
  updateCurrentSessionRuntimeBar();
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
  const activeStep = runtime.active_step || {};
  const stepSummary = String(activeStep.summary || "").trim();
  const summary = (runtime.summary || "").trim();
  if (stepSummary && status !== "idle") return stepSummary;
  if (summary && status !== "idle") return summary;
  if (status === "queued") return "Queued";
  if (status === "running") return "Working";
  if (status === "waiting_user") return "Waiting for you";
  if (status === "completed") return "";
  if (status === "failed") return runtime.last_error || "Failed";
  if (status === "stopped") return "";
  if (status === "offline" || status === "stale") return "Syncing";
  return "";
}

const _ACTIVE_RUNTIME_STATES = new Set(["queued", "running", "waiting_user", "paused", "offline", "stale"]);

function _activeRuntimeChildRuns(runtime = {}) {
  const childRuns = Array.isArray(runtime?.child_runs) ? runtime.child_runs : [];
  return childRuns.filter((item) => _ACTIVE_RUNTIME_STATES.has(String(item?.state || "").trim()));
}

function _runtimeDisplay(runtime = {}) {
  runtime = runtime || {};
  // Prefer server-computed display_state (eliminates client-side inference).
  // Fall back to client-side child-run promotion for older server versions.
  const serverDisplay = String(runtime.display_state || "").trim();
  const baseStatus = String(runtime.status || "idle").trim() || "idle";
  const activeChildren = _activeRuntimeChildRuns(runtime);

  if (serverDisplay) {
    const effectiveStatus = serverDisplay;
    const childCount = activeChildren.length;
    const summary = effectiveStatus !== baseStatus
      ? (activeChildren[0] ? (
          String(activeChildren[0].active_step?.summary || "").trim()
          || String(activeChildren[0].summary || "").trim()
          || (childCount > 1 ? `${childCount} background tasks still active` : "Background work still running")
        ) : sessionRuntimeSummary({ ...runtime, status: effectiveStatus }))
      : sessionRuntimeSummary(runtime);
    return {
      status: effectiveStatus,
      label: sessionRuntimeLabel({ status: effectiveStatus }),
      summary,
      activeChildCount: childCount,
    };
  }

  // Legacy fallback: infer from child runs when main status is terminal.
  if (activeChildren.length && ["idle", "completed", "stopped"].includes(baseStatus)) {
    const child = activeChildren[0] || {};
    const childStatus = String(child.state || "running").trim() || "running";
    const childStep = child.active_step || {};
    const childSummary =
      String(childStep.summary || "").trim()
      || String(child.summary || "").trim()
      || (activeChildren.length > 1
        ? `${activeChildren.length} background tasks still active`
        : "Background work still running");
    return {
      status: childStatus,
      label: sessionRuntimeLabel({ status: childStatus }),
      summary: childSummary,
      activeChildCount: activeChildren.length,
    };
  }

  return {
    status: baseStatus,
    label: sessionRuntimeLabel(runtime),
    summary: sessionRuntimeSummary(runtime),
    activeChildCount: activeChildren.length,
  };
}

function _shortRuntimeId(value = "") {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= 10) return text;
  return text.slice(-8);
}

function _runtimeTone(status = "") {
  const text = String(status || "").trim();
  if (text === "failed") return "error";
  if (text === "waiting_user" || text === "paused") return "wait";
  if (text === "completed") return "done";
  if (text === "superseded") return "queued";
  return "running";
}

function _runtimeStateLabel(status = "") {
  const text = String(status || "").trim();
  if (!text) return "Idle";
  if (text === "waiting_user") return "Waiting";
  return text
    .replace(/_/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function _runtimeMetaItems(runtime = {}) {
  const items = [];
  const threadId = _shortRuntimeId(runtime.thread_id);
  const runId = _shortRuntimeId(runtime.run_id);
  const activeStep = runtime.active_step || {};
  const stepType = String(activeStep.step_type || "").trim();
  const triggerType = String(runtime.trigger_type || "").trim();
  const pending = Number(runtime.pending_amendments || 0);
  if (threadId) items.push({ label: "Thread", value: threadId });
  if (runId) items.push({ label: "Run", value: runId });
  if (stepType) items.push({ label: "Step", value: _runtimeStateLabel(stepType) });
  if (triggerType) items.push({ label: "Trigger", value: _runtimeStateLabel(triggerType) });
  if (pending > 0) items.push({ label: "Queued", value: String(pending) });
  return items;
}

function _renderRuntimeMeta(runtime = {}) {
  const host = els.sessionRuntimeMeta;
  if (!host) return;
  const items = _runtimeMetaItems(runtime);
  host.classList.toggle("hidden", items.length === 0);
  if (!items.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = items.map((item) => (
    `<div class="session-runtime-meta-chip">`
      + `<span class="session-runtime-meta-label">${escHtml(item.label)}</span>`
      + `<span class="session-runtime-meta-value">${escHtml(item.value)}</span>`
    + `</div>`
  )).join("");
}

function _renderRuntimeStack(runtime = {}) {
  const host = els.sessionRuntimeStack;
  if (!host) return;
  const childRuns = Array.isArray(runtime.child_runs) ? runtime.child_runs : [];
  host.classList.toggle("hidden", childRuns.length === 0);
  if (!childRuns.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = childRuns.map((item) => {
    const runId = String(item.run_id || "").trim();
    const runState = String(item.state || "running").trim();
    const tone = _runtimeTone(runState);
    const title = String(item.agent_name || item.trigger_type || item.run_kind || "Child run").trim();
    const summary = String(item.active_step?.summary || item.summary || "").trim();
    const step = item.active_step || {};
    const expanded = Boolean(state._runtimeCardOpen?.[runId]);
    const focused = state._runtimeFocusedRunId === runId;
    const meta = [];
    if (item.run_kind) meta.push(_runtimeStateLabel(item.run_kind));
    if (item.visibility) meta.push(_runtimeStateLabel(item.visibility));
    if (item.thread_id) meta.push(`t:${_shortRuntimeId(item.thread_id)}`);
    return (
      `<article class="session-runtime-card${expanded ? " expanded" : ""}${focused ? " focused" : ""}" data-status="${escHtml(runState)}" data-run-id="${escHtml(runId)}">`
        + `<div class="session-runtime-card-head">`
          + `<div class="session-runtime-card-main">`
            + `<span class="session-runtime-card-dot" data-tone="${tone}"></span>`
            + `<span class="session-runtime-card-title">${escHtml(title)}</span>`
          + `</div>`
          + `<div class="session-runtime-card-head-actions">`
            + `<span class="session-runtime-card-state">${escHtml(_runtimeStateLabel(runState))}</span>`
            + `<button class="session-runtime-card-toggle" type="button" data-run-id="${escHtml(runId)}" aria-expanded="${expanded ? "true" : "false"}">`
              + `${expanded ? "Hide" : "Open"}`
            + `</button>`
          + `</div>`
        + `</div>`
        + (summary ? `<div class="session-runtime-card-summary">${escHtml(summary)}</div>` : "")
        + (meta.length ? `<div class="session-runtime-card-meta">${meta.map((part) => `<span>${escHtml(part)}</span>`).join("")}</div>` : "")
        + `<div class="session-runtime-card-detail${expanded ? "" : " hidden"}">`
          + `<div><span class="session-runtime-detail-label">Run</span><span class="session-runtime-detail-value">${escHtml(_shortRuntimeId(runId) || "—")}</span></div>`
          + `<div><span class="session-runtime-detail-label">Thread</span><span class="session-runtime-detail-value">${escHtml(_shortRuntimeId(item.thread_id) || "—")}</span></div>`
          + `<div><span class="session-runtime-detail-label">Step</span><span class="session-runtime-detail-value">${escHtml(_runtimeStateLabel(step.step_type || "idle"))}</span></div>`
          + `<div><span class="session-runtime-detail-label">Step State</span><span class="session-runtime-detail-value">${escHtml(_runtimeStateLabel(step.state || runState))}</span></div>`
        + `</div>`
      + `</article>`
    );
  }).join("");
}

function _isWorkbenchPreviewVisible() {
  return Boolean(els.workbenchPreview && !els.workbenchPreview.classList.contains("hidden"));
}

function _isWorkbenchFilesVisible() {
  return Boolean(els.workbenchFiles && !els.workbenchFiles.classList.contains("hidden"));
}

function _isWorkbenchActivityVisible() {
  return Boolean(els.workbenchActivity && !els.workbenchActivity.classList.contains("hidden"));
}

function _runtimeBarHasContent(label = "", summary = "", badge = "") {
  return Boolean(String(label || "").trim() || String(summary || "").trim() || String(badge || "").trim());
}

function _runtimeHasContent(runtime = null, feed = []) {
  const childRuns = Array.isArray(runtime?.child_runs) ? runtime.child_runs : [];
  return Boolean(runtime && (((runtime.status || "idle") !== "idle") || childRuns.length || feed.length));
}

function _isRuntimeMonitorVisible(runtime = null, feed = []) {
  return _runtimeHasContent(runtime, feed) && !state._runtimeMonitorHidden;
}

function _syncWorkbenchVisibility(runtimeVisible) {
  const workbenchVisible = Boolean(runtimeVisible || _isWorkbenchFilesVisible() || _isWorkbenchPreviewVisible() || _isWorkbenchActivityVisible());
  if (els.chatWorkbench) els.chatWorkbench.classList.toggle("hidden", !workbenchVisible);
  if (els.chatWorkspace) els.chatWorkspace.classList.toggle("workbench-active", workbenchVisible);
}

function _syncRuntimeMonitorButtons(hasContent, visible) {
  if (els.btnToggleRuntimeInline) {
    const label = visible ? "Hide runtime monitor" : "Show runtime monitor";
    els.btnToggleRuntimeInline.classList.toggle("hidden", !hasContent);
    els.btnToggleRuntimeInline.classList.toggle("active", visible);
    els.btnToggleRuntimeInline.title = label;
    els.btnToggleRuntimeInline.setAttribute("aria-label", label);
    els.btnToggleRuntimeInline.setAttribute("aria-expanded", visible ? "true" : "false");
  }
}

function _scrollRuntimeLogToLatest() {
  if (!els.sessionRuntimeLog || els.sessionRuntimeLog.classList.contains("hidden")) return;
  requestAnimationFrame(() => {
    if (!els.sessionRuntimeLog) return;
    els.sessionRuntimeLog.scrollTop = els.sessionRuntimeLog.scrollHeight;
  });
}

export function refreshWorkbenchVisibility() {
  const sid = getCurrentSessionId();
  const runtime = sid ? getSessionRuntime(sid) : null;
  const feed = sid ? (state._sessionRuntimeFeed[sid] || []) : [];
  const hasRuntime = _runtimeHasContent(runtime, feed);
  const runtimeVisible = _isRuntimeMonitorVisible(runtime, feed);
  _syncRuntimeMonitorButtons(hasRuntime, runtimeVisible);
  _syncWorkbenchVisibility(runtimeVisible);
}

export function pushWorkbenchActivity(item = {}) {
  const entry = {
    id: `${Date.now()}-${state._workbenchActivity.length + 1}`,
    level: String(item.level || "info"),
    title: String(item.title || "").trim(),
    summary: String(item.summary || "").trim(),
    meta: String(item.meta || "").trim(),
    ts: Number(item.ts || Date.now()),
    actionType: String(item.actionType || "").trim(),
    sessionId: String(item.sessionId || "").trim(),
    artifactName: String(item.artifactName || "").trim(),
    artifactUrl: String(item.artifactUrl || "").trim(),
    artifactKind: String(item.artifactKind || "").trim(),
    artifactSource: String(item.artifactSource || "").trim(),
    group: String(item.group || "").trim(),
    read: Boolean(item.read),
  };
  state._workbenchActivity.unshift(entry);
  if (state._workbenchActivity.length > 14) state._workbenchActivity.splice(14);
  renderWorkbenchActivity();
}

export function markWorkbenchActivityRead(id) {
  const key = String(id || "").trim();
  if (!key) return;
  const entry = state._workbenchActivity.find((item) => item.id === key);
  if (!entry || entry.read) return;
  entry.read = true;
  renderWorkbenchActivity();
}

export function dismissWorkbenchActivity(id) {
  const key = String(id || "").trim();
  if (!key) return;
  const before = state._workbenchActivity.length;
  state._workbenchActivity = state._workbenchActivity.filter((item) => item.id !== key);
  if (state._workbenchActivity.length !== before) renderWorkbenchActivity();
}

export function setRuntimeFocusRun(runId = "") {
  state._runtimeFocusedRunId = String(runId || "").trim();
  _persistRuntimeUiForSession(getCurrentSessionId());
  updateCurrentSessionRuntimeBar();
}

function _groupWorkbenchActivityItems(items = []) {
  const groups = {
    attention: [],
    results: [],
    background: [],
  };
  for (const item of items) {
    const explicit = String(item.group || "").trim();
    if (explicit === "attention" || explicit === "results" || explicit === "background") {
      groups[explicit].push(item);
      continue;
    }
    if (["wait", "warn", "error"].includes(item.level)) {
      groups.attention.push(item);
    } else if (["artifact", "done"].includes(item.level)) {
      groups.results.push(item);
    } else {
      groups.background.push(item);
    }
  }
  return groups;
}

export function renderWorkbenchActivity() {
  const host = els.workbenchActivityBody;
  const card = els.workbenchActivity;
  const attentionStrip = els.workbenchAttentionStrip;
  if (!host || !card) return;
  const items = state._workbenchActivity || [];
  card.classList.toggle("hidden", items.length === 0);
  if (!items.length) {
    if (attentionStrip) {
      attentionStrip.innerHTML = "";
      attentionStrip.classList.add("hidden");
    }
    host.innerHTML = "";
    refreshWorkbenchVisibility();
    return;
  }
  const groups = _groupWorkbenchActivityItems(items);
  const urgentItems = (groups.attention || []).filter((item) => !item.read).slice(0, 3);
  if (attentionStrip) {
    attentionStrip.classList.toggle("hidden", urgentItems.length === 0);
    attentionStrip.innerHTML = urgentItems.map((item) => {
      const actionAttrs = item.actionType
        ? ` data-action-type="${escHtml(item.actionType)}" data-session-id="${escHtml(item.sessionId)}" data-artifact-name="${escHtml(item.artifactName)}" data-artifact-url="${escHtml(item.artifactUrl)}" data-artifact-kind="${escHtml(item.artifactKind)}" data-artifact-source="${escHtml(item.artifactSource)}"`
        : "";
      return (
        `<button class="workbench-attention-chip${item.actionType ? " actionable" : ""}" type="button" data-activity-id="${escHtml(item.id)}"${actionAttrs}>`
          + `<span class="workbench-attention-chip-dot" data-level="${escHtml(item.level)}"></span>`
          + `<span class="workbench-attention-chip-label">${escHtml(item.title || "Attention")}</span>`
        + `</button>`
      );
    }).join("");
  }
  const sections = [
    { key: "attention", title: "Needs Attention", subtitle: "Things that may need an explicit action." },
    { key: "results", title: "Recent Results", subtitle: "Artifacts and finished outputs ready to inspect." },
    { key: "background", title: "Background Updates", subtitle: "Status changes from runs continuing behind the scenes." },
  ];
  host.innerHTML = sections.map((section) => {
    const entries = groups[section.key] || [];
    if (!entries.length) return "";
    const unread = entries.filter((item) => !item.read).length;
    const itemsHtml = entries.map((item) => {
    const title = escHtml(item.title || "Update");
    const summary = escHtml(item.summary || "");
    const meta = escHtml(item.meta || "");
    const actionAttrs = item.actionType
      ? ` data-action-type="${escHtml(item.actionType)}" data-session-id="${escHtml(item.sessionId)}" data-artifact-name="${escHtml(item.artifactName)}" data-artifact-url="${escHtml(item.artifactUrl)}" data-artifact-kind="${escHtml(item.artifactKind)}" data-artifact-source="${escHtml(item.artifactSource)}"`
      : "";
    return (
      `<article class="workbench-activity-item${item.actionType ? " actionable" : ""}${item.read ? "" : " unread"}" data-level="${escHtml(item.level)}" data-activity-id="${escHtml(item.id)}"${actionAttrs}>`
        + `<div class="workbench-activity-item-head">`
          + `<div class="workbench-activity-item-head-main">`
            + `<span class="workbench-activity-item-dot"></span>`
            + `<span class="workbench-activity-item-title">${title}</span>`
          + `</div>`
          + `<button class="workbench-activity-dismiss" type="button" data-dismiss-activity="${escHtml(item.id)}" aria-label="Dismiss activity">×</button>`
        + `</div>`
        + (summary ? `<div class="workbench-activity-item-summary">${summary}</div>` : "")
        + (meta ? `<div class="workbench-activity-item-meta">${meta}</div>` : "")
      + `</article>`
    );
    }).join("");
    return (
      `<section class="workbench-activity-group" data-group="${section.key}">`
        + `<div class="workbench-activity-group-head">`
          + `<div class="workbench-activity-group-copy">`
            + `<div class="workbench-activity-group-title">${escHtml(section.title)}</div>`
            + `<div class="workbench-activity-group-subtitle">${escHtml(section.subtitle)}</div>`
          + `</div>`
          + `<div class="workbench-activity-group-count">${unread > 0 ? `${unread} new` : `${entries.length}`}</div>`
        + `</div>`
        + `<div class="workbench-activity-group-body">${itemsHtml}</div>`
      + `</section>`
    );
  }).join("");
  refreshWorkbenchVisibility();
}

export function updateCurrentSessionRuntimeBar() {
  const bar = els.sessionRuntimeBar;
  if (!bar) return;
  const sid = getCurrentSessionId();
  const runtime = sid ? getSessionRuntime(sid) : null;
  const feed = sid ? (state._sessionRuntimeFeed[sid] || []) : [];
  const display = _runtimeDisplay(runtime);
  const status = display.status || runtime?.status || "idle";
  const childRuns = Array.isArray(runtime?.child_runs) ? runtime.child_runs : [];
  const hasContent = _runtimeHasContent(runtime, feed);
  const visible = hasContent && !state._runtimeMonitorHidden;
  if (els.runtimeMonitor) {
    els.runtimeMonitor.classList.toggle("hidden", !visible);
  }
  _syncRuntimeMonitorButtons(hasContent, visible);
  _syncWorkbenchVisibility(visible);
  if (els.sessionRuntimeLog) {
    els.sessionRuntimeLog.classList.toggle("hidden", !(visible && state._sessionRuntimeLogOpen && feed.length));
  }
  if (els.sessionRuntimeMeta) els.sessionRuntimeMeta.classList.toggle("hidden", !visible);
  if (els.sessionRuntimeStack) els.sessionRuntimeStack.classList.toggle("hidden", !(visible && childRuns.length));
  if (!visible) {
    bar.classList.add("hidden");
    if (els.sessionRuntimeMeta) els.sessionRuntimeMeta.innerHTML = "";
    if (els.sessionRuntimeStack) els.sessionRuntimeStack.innerHTML = "";
    if (els.sessionRuntimeLog) els.sessionRuntimeLog.innerHTML = "";
    return;
  }
  bar.dataset.status = status;
  const labelText = display.label || "";
  const summaryText = display.summary || "";
  if (els.sessionRuntimeLabel) els.sessionRuntimeLabel.textContent = labelText;
  if (els.sessionRuntimeSummary) els.sessionRuntimeSummary.textContent = summaryText;
  let badgeText = "";
  if (els.sessionRuntimeBadge) {
    const pending = Number(runtime?.pending_amendments || 0);
    const focus = state._runtimeFocusedRunId ? `Focused ${_shortRuntimeId(state._runtimeFocusedRunId)}` : "";
    badgeText = focus || (pending > 0 ? `${pending} queued` : "") || (display.activeChildCount > 1 ? `${display.activeChildCount} active` : "");
    els.sessionRuntimeBadge.textContent = badgeText;
    els.sessionRuntimeBadge.classList.toggle("hidden", !badgeText);
  }
  bar.classList.toggle("hidden", !_runtimeBarHasContent(labelText, summaryText, badgeText));
  if (els.sessionRuntimeToggle) {
    els.sessionRuntimeToggle.setAttribute("aria-expanded", state._sessionRuntimeLogOpen ? "true" : "false");
    els.sessionRuntimeToggle.textContent = state._sessionRuntimeLogOpen ? "Collapse" : "Expand";
  }
  _renderRuntimeMeta(runtime);
  _renderRuntimeStack(runtime);
  if (els.sessionRuntimeLog) {
    const focusedRun = state._runtimeFocusedRunId;
    const visibleFeed = focusedRun
      ? feed.filter((item) => !item.child_run_id || item.child_run_id === focusedRun || item.run_id === focusedRun)
      : feed;
    els.sessionRuntimeLog.innerHTML = visibleFeed.map((item) => {
      const label = escHtml(String(item.label || "").trim());
      const summary = escHtml(String(item.summary || "").trim());
      const prefix = item.scope === "child" ? `<span class="session-runtime-log-scope">Child</span>` : "";
      const body = summary ? `${label ? `${label} · ` : ""}${summary}` : label;
      return `<div class="session-runtime-log-item" data-level="${item.level}">${prefix}${body}</div>`;
    }).join("");
    _scrollRuntimeLogToLatest();
  }
}

if (els.sessionRuntimeToggle) {
  els.sessionRuntimeToggle.addEventListener("click", () => {
    setSessionRuntimeLogOpen(!state._sessionRuntimeLogOpen);
  });
}

if (els.btnToggleRuntimeInline) {
  els.btnToggleRuntimeInline.addEventListener("click", () => {
    const sid = getCurrentSessionId();
    if (!sid) return;
    state._runtimeMonitorHidden = !state._runtimeMonitorHidden;
    _persistRuntimeUiForSession(sid);
    updateCurrentSessionRuntimeBar();
  });
}

if (els.sessionRuntimeStack) {
  els.sessionRuntimeStack.addEventListener("click", (ev) => {
    const target = ev.target instanceof Element ? ev.target : null;
    if (!target) return;
    const btn = target.closest(".session-runtime-card-toggle[data-run-id]");
    if (btn) {
      const runId = String(btn.getAttribute("data-run-id") || "").trim();
      if (!runId) return;
      state._runtimeCardOpen[runId] = !state._runtimeCardOpen[runId];
      _persistRuntimeUiForSession(getCurrentSessionId());
      updateCurrentSessionRuntimeBar();
      return;
    }
    const card = target.closest(".session-runtime-card[data-run-id]");
    if (!card) return;
    const runId = String(card.getAttribute("data-run-id") || "").trim();
    if (!runId) return;
    state._runtimeFocusedRunId = state._runtimeFocusedRunId === runId ? "" : runId;
    _persistRuntimeUiForSession(getCurrentSessionId());
    updateCurrentSessionRuntimeBar();
  });
}

if (els.workbenchActivityBody) {
  const _handleActivityAction = (ev) => {
    const target = ev.target instanceof Element ? ev.target : null;
    if (!target) return;
    const dismiss = target.closest(".workbench-activity-dismiss[data-dismiss-activity]");
    if (dismiss) {
      dismissWorkbenchActivity(dismiss.getAttribute("data-dismiss-activity") || "");
      ev.stopPropagation();
      return;
    }
    const card = target.closest(".workbench-activity-item.actionable[data-action-type]");
    if (!card) return;
    markWorkbenchActivityRead(card.getAttribute("data-activity-id") || "");
    document.dispatchEvent(new CustomEvent("hc:workbench-activity-action", {
      detail: {
        actionType: card.getAttribute("data-action-type") || "",
        sessionId: card.getAttribute("data-session-id") || "",
        artifact: {
          name: card.getAttribute("data-artifact-name") || "",
          url: card.getAttribute("data-artifact-url") || "",
          kind: card.getAttribute("data-artifact-kind") || "",
          source: card.getAttribute("data-artifact-source") || "",
        },
      },
    }));
  };
  els.workbenchActivityBody.addEventListener("click", _handleActivityAction);
  els.workbenchAttentionStrip?.addEventListener("click", _handleActivityAction);
}

if (els.input && !getCurrentSessionId()) {
  restoreComposerDraft("");
}

export function debugUiLifecycle(event, extra = {}) {
  try {
    if (localStorage.getItem("hushclaw.debug.ui") !== "1") return;
  } catch {
    return;
  }
  console.debug("[hushclaw-ui]", event, extra);
}
