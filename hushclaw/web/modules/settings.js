/**
 * settings.js — Settings modal: 5-tab renderer, config status handler, save logic.
 */

import {
  state, wizard, connectors, browser, emailCfg, calendarCfg,
  els, send, escHtml, clearCurrentSessionId,
} from "./state.js";
import {
  bindThemeControls, bindThemeSwatches,
  getThemeMode, getTheme, THEMES, THEME_LABELS,
} from "./theme.js";
import { resetChatSessionUiState } from "./chat.js";
import {
  maybeAutoCheckUpdates, refreshUpdateUi, requestCheckUpdate, requestRunUpdate,
} from "./updates.js";

// ── Pending-request timers (reset on WS reconnect) ─────────────────────────

let _wizardSaveTimer = null;
let _testTimer       = null;

// ── Transsion auth state (module-private, isolated from shared wizard) ──────
// These are kept here because they drive the LLM-provider login UI.
// The community pf-sso token is owned by transsion/auth.js, not this module.
let _txEmail           = "";
let _txDisplayName     = "";
let _txCodeRequested   = false;
let _txShowRelogin     = false;
// Kept only long enough to include in TOML save; forum plugin owns the live copy.
let _txAccessToken     = "";

/** Called by websocket.js on ws.onopen to discard stale pending saves/tests. */
export function resetWizardTimers() {
  clearTimeout(_wizardSaveTimer); _wizardSaveTimer = null;
  clearTimeout(_testTimer);       _testTimer = null;
}

// ── Provider definitions ───────────────────────────────────────────────────

export const PROVIDERS = [
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
    id: "minimax",
    name: "MiniMax",
    desc: "MiniMax M2 series — OpenAI-compatible API. 204K context, fast & high-speed variants.",
    needsKey: true,
    defaultModel: "MiniMax-M2.7",
    modelSuggestions: [
      "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
      "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
      "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
      "MiniMax-M2",
    ],
    keyLabel: "API Key",
    keyPlaceholder: "eyJ…",
    keyHint: 'Get your key from <a href="https://platform.minimax.io" target="_blank" rel="noopener">platform.minimax.io</a> (global) or <a href="https://platform.minimaxi.com" target="_blank" rel="noopener">platform.minimaxi.com</a> (China)',
    defaultBaseUrl: "https://api.minimax.io/v1",
    baseUrlLabel: "Base URL",
    regions: [
      { label: "🌏 China",  url: "https://api.minimaxi.com/v1" },
      { label: "🌍 Global", url: "https://api.minimax.io/v1"  },
    ],
  },
  {
    id: "gemini",
    name: "Google Gemini",
    desc: "Gemini 2.5 Flash / Pro via Google's official SDK. Requires: pip install 'hushclaw[gemini]'",
    needsKey: true,
    defaultModel: "gemini-2.5-flash-preview-04-17",
    modelSuggestions: [
      "gemini-2.5-flash-preview-04-17",
      "gemini-2.5-pro-preview-05-06",
      "gemini-2.0-flash",
      "gemini-1.5-pro",
      "gemini-1.5-flash",
    ],
    keyLabel: "API Key",
    keyPlaceholder: "AIza…",
    keyHint: 'Get your key from <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener">Google AI Studio</a>. Also accepts the <code>GEMINI_API_KEY</code> env var.',
    defaultBaseUrl: "",
    baseUrlLabel: "Base URL (leave blank for default)",
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
    id: "transsion",
    name: "Transsion / TEX AI Router",
    desc: "TEX AI Router — enterprise multi-model gateway (Azure GPT / Google Gemini / ByteDance Doubao). Login with your @transsion.com email.",
    needsKey: false,
    authFlow: "email_code",
    defaultModel: "azure/gpt-4o-mini",
    modelSuggestions: [
      "azure/gpt-4.1", "azure/gpt-4.1-mini", "azure/gpt-4o-mini",
      "azure/gpt-5.4", "azure/gpt-5.4-mini",
      "google/gemini-2.5-flash-lite", "google/gemini-3-flash-preview",
    ],
    keyLabel: "",
    keyPlaceholder: "",
    keyHint: "Login with your Transsion enterprise email to obtain API credentials automatically.",
    defaultBaseUrl: "https://airouter.aibotplatform.com/v1",
    baseUrlLabel: "TEX Router endpoint",
  },
];

export function providerById(id) {
  const ALIASES = {
    "openai-raw":    "openai-sdk",
    "anthropic-sdk": "anthropic-raw",
    "aigocode-raw":  "anthropic-raw",
    "aigocode":      "anthropic-raw",
    "google":        "gemini",
    "tex":           "transsion",
  };
  const normalised = ALIASES[id] || id;
  return PROVIDERS.find((p) => p.id === normalised) || PROVIDERS[0];
}

// ── Channel definitions ────────────────────────────────────────────────────

function _credHint(isSet) {
  return isSet
    ? '<span class="conn-set-badge">SET</span> Leave blank to keep current value.'
    : "";
}

/** Returns true if the connector has at least one credential already stored. */
function _isConfigured(platform, c) {
  switch (platform) {
    case "telegram":  return c.bot_token_set || !!c.bot_token;
    case "feishu":    return c.app_secret_set || !!(c.app_id && c.app_secret);
    case "discord":   return c.bot_token_set || !!c.bot_token;
    case "slack":     return c.bot_token_set || c.app_token_set || !!(c.bot_token && c.app_token);
    case "dingtalk":  return c.client_secret_set || !!(c.client_id && c.client_secret);
    case "wecom":     return c.corp_secret_set || !!(c.corp_id && c.corp_secret);
    default:          return false;
  }
}

export const CHANNELS = [
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="tg-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Convert Markdown formatting to Telegram HTML (bold, italic, code blocks, links).</div>
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies <span class="wfield-optional">(reserved)</span></label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="fs-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Feishu text messages do not render Markdown natively. Reserved for future use.</div>
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="dc-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Discord renders standard Markdown automatically — no conversion needed.</div>
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="sl-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Send responses as Slack mrkdwn blocks (bold, italic, code, links).</div>
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="dt-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Send responses as DingTalk Markdown messages (title + formatted text).</div>
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
      </div>
      <div class="wfield wfield-row">
        <label>Markdown replies</label>
        <label class="toggle-switch toggle-inline">
          <input type="checkbox" id="wc-markdown" ${c.markdown !== false ? "checked" : ""}>
          <span class="toggle-slider"></span>
        </label>
        <div class="wfield-hint">Send responses as WeCom Markdown messages (bold, links, mentions).</div>
      </div>`,
  },
];

// ── Test connection spinner ────────────────────────────────────────────────

const _TEST_STEP_ICONS = {
  running: '<span class="test-step-spinner">⠋</span>',
  ok:      '<span class="test-step-icon ok">✓</span>',
  warn:    '<span class="test-step-icon warn">⚠</span>',
  error:   '<span class="test-step-icon error">✗</span>',
  skip:    '<span class="test-step-icon skip">–</span>',
};
const _TEST_SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
let _testSpinnerFrame = 0;
let _testSpinnerTimer = null;

function _startSpinner(stepId) {
  _stopSpinner();
  _testSpinnerFrame = 0;
  _testSpinnerTimer = setInterval(() => {
    _testSpinnerFrame = (_testSpinnerFrame + 1) % _TEST_SPINNERS.length;
    const el = document.querySelector(`#wiz-test-steps [data-step="${stepId}"] .test-step-spinner`);
    if (el) el.textContent = _TEST_SPINNERS[_testSpinnerFrame];
  }, 80);
}

function _stopSpinner() {
  if (_testSpinnerTimer) { clearInterval(_testSpinnerTimer); _testSpinnerTimer = null; }
}

export function handleTestProviderStep(data) {
  const container = document.getElementById("wiz-test-steps");
  if (!container) return;

  const { step, status, label, detail } = data;
  let row = container.querySelector(`[data-step="${step}"]`);

  if (!row) {
    row = document.createElement("div");
    row.className = "test-step-row";
    row.dataset.step = step;
    container.appendChild(row);
  }

  if (status === "running") _startSpinner(step);
  else _stopSpinner();

  row.className = `test-step-row status-${status}`;
  row.innerHTML = `
    ${_TEST_STEP_ICONS[status] || ""}
    <span class="test-step-label">${escHtml(label)}</span>
    <span class="test-step-detail">${escHtml(detail)}</span>
  `;
}

export function handleTestProviderResult(data) {
  clearTimeout(_testTimer);
  _testTimer = null;
  _stopSpinner();
  const testBtn = document.getElementById("wiz-test-btn");
  if (testBtn) { testBtn.disabled = false; testBtn.textContent = "Test Connection"; }

  const container = document.getElementById("wiz-test-steps");
  if (!container) return;

  const summary = document.createElement("div");
  summary.className = `test-step-summary ${data.ok ? "ok" : "error"}`;
  summary.textContent = data.ok
    ? "✓ " + (data.detail || "All checks passed.")
    : "✗ " + (data.detail || "Connection failed.");
  container.appendChild(summary);
}

// ── Config status handler ──────────────────────────────────────────────────

export function handleConfigStatus(cfg) {
  wizard.serverConfig = cfg;
  window.__HUSHCLAW_PUBLIC_BASE_URL = cfg.public_base_url || "";

  if (!wizard.open || wizard._pendingRefresh) {
    wizard._pendingRefresh = false;
    const prov = providerById(cfg.provider);
    wizard.provider      = prov.id;
    wizard.model         = cfg.model || prov.defaultModel;
    wizard.baseUrl       = cfg.base_url || prov.defaultBaseUrl || "";
    wizard.apiKey        = "";
    wizard.maxTokens     = cfg.max_tokens     ?? 4096;
    wizard.maxToolRounds = cfg.max_tool_rounds ?? 40;
    wizard.systemPrompt  = cfg.system_prompt  || "";
    wizard.costIn        = cfg.cost_per_1k_input_tokens  || 0.0;
    wizard.costOut       = cfg.cost_per_1k_output_tokens || 0.0;
    const txn = cfg.transsion || {};
    _txEmail       = txn.email         || "";
    _txDisplayName = txn.display_name  || "";
    _txAccessToken = txn.access_token  || "";
    // Notify the Forum plugin about the saved login state so it can show
    // its tab immediately on page load without requiring a fresh login.
    if (_txAccessToken && txn.authed) {
      document.dispatchEvent(new CustomEvent("hc:transsion-authed", {
        detail: {
          accessToken: _txAccessToken,
          email:       _txEmail,
          displayName: _txDisplayName,
        },
      }));
    }
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
    wizard.toolsProfile = cfg.tools_profile   || "";
    wizard.workspaceDir = cfg.workspace_dir    || "";
    wizard.workspaceStatus = cfg.workspace || {configured: false, path: "", soul_md: false, user_md: false};
    // api_keys: load booleans for display + raw values for pre-fill
    wizard.apiKeys = Object.assign({}, cfg._api_keys_raw || {});
    wizard.theme     = getTheme();
    wizard.themeMode = getThemeMode();
    const upd = cfg.update || {};
    wizard.updateAutoCheckEnabled = upd.auto_check_enabled ?? true;
    wizard.updateCheckIntervalHours = upd.check_interval_hours ?? 24;
    wizard.updateChannel = upd.channel || "stable";
    wizard.updateCurrentVersion = upd.current_version || "";
    wizard.updateLatestVersion = upd.latest_version || "";
    wizard.updateAvailable = Boolean(upd.update_available);
    wizard.updateReleaseUrl = upd.release_url || "";
    wizard.updateLastCheckedAt = Math.max(
      Number(upd.last_checked_at || 0),
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
    connectors.feishu.allowlist              = (fs.allowlist || []).join(", ");
    connectors.feishu.stream                 = Boolean(fs.stream);
    connectors.feishu.markdown               = fs.markdown !== false;

    const dc = cfg.connectors.discord || {};
    connectors.discord.enabled          = Boolean(dc.enabled);
    connectors.discord.bot_token        = "";
    connectors.discord.bot_token_set    = Boolean(dc.bot_token_set);
    connectors.discord.agent            = dc.agent || "default";
    connectors.discord.allowlist        = (dc.allowlist || []).join(", ");
    connectors.discord.guild_allowlist  = (dc.guild_allowlist || []).join(", ");
    connectors.discord.require_mention  = dc.require_mention !== false;
    connectors.discord.stream           = dc.stream !== false;
    connectors.discord.markdown         = dc.markdown !== false;

    const sl = cfg.connectors.slack || {};
    connectors.slack.enabled            = Boolean(sl.enabled);
    connectors.slack.bot_token          = "";
    connectors.slack.bot_token_set      = Boolean(sl.bot_token_set);
    connectors.slack.app_token          = "";
    connectors.slack.app_token_set      = Boolean(sl.app_token_set);
    connectors.slack.agent              = sl.agent || "default";
    connectors.slack.allowlist          = (sl.allowlist || []).join(", ");
    connectors.slack.stream             = sl.stream !== false;
    connectors.slack.markdown           = sl.markdown !== false;

    const dt = cfg.connectors.dingtalk || {};
    connectors.dingtalk.enabled           = Boolean(dt.enabled);
    connectors.dingtalk.client_id         = dt.client_id || "";
    connectors.dingtalk.client_secret     = "";
    connectors.dingtalk.client_secret_set = Boolean(dt.client_secret_set);
    connectors.dingtalk.agent             = dt.agent || "default";
    connectors.dingtalk.allowlist         = (dt.allowlist || []).join(", ");
    connectors.dingtalk.stream            = dt.stream !== false;
    connectors.dingtalk.markdown          = dt.markdown !== false;

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
    connectors.wecom.markdown           = wc.markdown !== false;
  }

  if (cfg.browser) {
    browser.enabled                = cfg.browser.enabled ?? true;
    browser.headless               = cfg.browser.headless ?? true;
    browser.timeout                = cfg.browser.timeout ?? 30;
    browser.playwright_installed   = cfg.browser.playwright_installed ?? false;
    browser.use_user_chrome        = cfg.browser.use_user_chrome ?? false;
    browser.remote_debugging_url   = cfg.browser.remote_debugging_url ?? "";
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
    openWizard(false);
  }
  maybeAutoCheckUpdates(cfg);
}

export function handleConfigSaved(data) {
  console.info(
    "[hushclaw:save] config_saved ok=%s save_client_id=%s error=%s",
    data.ok,
    data.save_client_id ?? "(none)",
    data.error || "",
  );
  clearTimeout(_wizardSaveTimer);
  _wizardSaveTimer = null;
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

// ── Transsion auth flow handlers ──────────────────────────────────────────

function _txStatus(msg, kind = "info") {
  const el = document.getElementById("tx-status");
  if (!el) return;
  el.style.display = "block";
  el.className = `transsion-status transsion-status-${kind}`;
  el.textContent = msg;
}

/** Reset Transsion wizard buttons after a failed WS `error` (send code / login in progress). */
export function resetTranssionPendingUi(errorMessage = "") {
  const codeField = document.getElementById("tx-code-field");
  const sendBtn = document.getElementById("tx-send-code-btn");
  let touched = false;
  if (sendBtn && sendBtn.textContent === "Sending…") {
    touched = true;
    sendBtn.disabled = false;
    const showResend = codeField && codeField.style.display !== "none";
    sendBtn.textContent = showResend ? "Resend Code" : "Send Code";
  }
  const loginBtn = document.getElementById("tx-login-btn");
  if (loginBtn && loginBtn.textContent === "Logging in…") {
    touched = true;
    loginBtn.disabled = false;
    loginBtn.textContent = "Login & Authorize";
  }
  if (touched && errorMessage) {
    _txStatus(errorMessage, "error");
  }
}

export function handleTransssionCodeSent(data) {
  _txCodeRequested = true;
  const sendBtn = document.getElementById("tx-send-code-btn");
  if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "Resend Code"; }
  const codeField = document.getElementById("tx-code-field");
  if (codeField) codeField.style.display = "";
  const hint = document.getElementById("tx-send-hint");
  if (hint) hint.textContent = `Code sent to ${data.email || "your email"}.`;
  _txStatus("Verification code sent — check your inbox.", "info");
}

// ── Settings widget registry (for plugin injection into Channels tab) ──────
const _settingsWidgets = [];
/** Register a function that receives the Channels-tab container and appends its own widget. */
export function registerSettingsWidget(fn) { _settingsWidgets.push(fn); }

export function handleTransssionAuthed(data) {
  const loginBtn = document.getElementById("tx-login-btn");
  if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = "Login & Authorize"; }
  const name = data.display_name || data.email || "user";
  const quota = data.quota_remain ? ` · Quota: ${data.quota_remain}` : "";

  wizard.apiKey = (data.api_key || "").trim();
  wizard.baseUrl = (data.base_url || "").trim() || wizard.baseUrl;
  _txEmail       = (data.email        || "").trim();
  _txDisplayName = (data.display_name || "").trim();
  _txAccessToken = (data.access_token || "").trim();
  // Dispatch plugin-friendly event — transsion/ plugin listens for this
  // to persist the community SSO token in its own localStorage store.
  document.dispatchEvent(new CustomEvent("hc:transsion-authed", {
    detail: {
      accessToken:  _txAccessToken,
      email:        _txEmail,
      displayName:  _txDisplayName,
    },
  }));
  // Collapse re-login form back to compact badge on successful auth
  _txShowRelogin = false;

  const burlEl = document.getElementById("wiz-baseurl");
  if (burlEl && wizard.baseUrl) burlEl.value = wizard.baseUrl;

  // Seed datalist / chips from credential response; then refresh from GET /v1/models.
  if (Array.isArray(data.models) && data.models.length) {
    const listEl = document.getElementById("wiz-model-list");
    if (listEl) {
      listEl.innerHTML = data.models.map((m) => `<option value="${escHtml(m)}">`).join("");
    }
    const modelEl = document.getElementById("wiz-model");
    const first = data.models[0];
    if (modelEl) {
      wizard.model = first;
      modelEl.value = first;
    }
    const chipsContainer = document.querySelector(".settings-section .model-chip")?.parentElement;
    if (chipsContainer) {
      chipsContainer.innerHTML = data.models.slice(0, 12).map(
        (m) => `<button type="button" class="secondary model-chip" data-model="${escHtml(m)}">${escHtml(m)}</button>`
      ).join("");
      chipsContainer.querySelectorAll(".model-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          wizard.model = chip.dataset.model;
          const me = document.getElementById("wiz-model");
          const sel = document.getElementById("wiz-model-select");
          if (me) me.value = chip.dataset.model;
          if (sel && sel.style.display !== "none") sel.value = chip.dataset.model;
        });
      });
    }
  }

  renderModelTab();
  _txStatus(`✓ Signed in as ${name}${quota}. Choose a model, then click Save.`, "ok");
}

export function openWizard(dismissible = true) {
  wizard.open        = true;
  wizard.dismissible = dismissible;
  els.wizardOverlay.classList.remove("hidden");
  els.wbtnClose.style.display = (dismissible || wizard.savedOnce) ? "" : "none";
  renderSettingsModal();
}

export function closeWizard() {
  wizard.open = false;
  els.wizardOverlay.classList.add("hidden");
}

// ── Settings tab rendering ─────────────────────────────────────────────────

export function renderSettingsTabs() {
  const tabs = [
    { id: "model",        label: "Model" },
    { id: "channels",     label: "Channels" },
    { id: "system",       label: "System" },
    { id: "memory",       label: "Memory" },
    { id: "integrations", label: "Integrations" },
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

export function renderSettingsModal() {
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

export function renderModelTab() {
  const prov = providerById(wizard.provider);
  const sc   = wizard.serverConfig;

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

  let keyHtml = `<div class="settings-section"><h3 class="settings-section-h">API Key &amp; Endpoint</h3>`;

  if (prov.authFlow === "email_code") {
    // ── Transsion two-step email-code login UI ──────────────────────────────
    const ts = sc && sc.transsion;
    const savedAuthed = ts && ts.authed;
    const showRelogin = _txShowRelogin;
    const pendingSave =
      wizard.provider === "transsion" &&
      Boolean(wizard.apiKey && _txEmail) &&
      !savedAuthed;
    const displaySaved = savedAuthed ? escHtml(ts.display_name || ts.email) : "";

    // Authed badge: show compact state when authenticated and not in re-login mode
    let topBadge = "";
    if (savedAuthed && !showRelogin) {
      topBadge = `
        <div class="transsion-authed-badge">
          <span>&#10003; Saved &middot; signed in as <strong>${displaySaved}</strong> (${escHtml(ts.email)})</span>
          <button type="button" id="tx-relogin-btn" class="transsion-relogin-btn">Re-login</button>
        </div>`;
    } else if (savedAuthed && showRelogin) {
      topBadge = `
        <div class="transsion-authed-badge transsion-authed-badge-dim">
          <span>Refreshing credentials for <strong>${displaySaved}</strong> (${escHtml(ts.email)})</span>
          <button type="button" id="tx-cancel-relogin-btn" class="transsion-relogin-btn">Cancel</button>
        </div>`;
    } else if (pendingSave) {
      topBadge = `<div class="transsion-pending-save">Signed in — pick a model below, then click <strong>Save</strong> at the bottom to store credentials.</div>`;
    }

    // Show the OTP form only when: not savedAuthed, OR user explicitly clicked Re-login
    const showForm = !savedAuthed || showRelogin;
    const emailValue = escHtml((ts && ts.email) || _txEmail || "");
    const codeHidden = _txCodeRequested ? "" : "display:none";

    keyHtml += `
      ${topBadge}
      <div id="tx-login-form" style="${showForm ? "" : "display:none"}">
        <div class="wfield">
          <label>Transsion Enterprise Email</label>
          <div style="display:flex;gap:8px">
            <input type="email" id="tx-email" autocomplete="off"
                   placeholder="you@transsion.com" style="flex:1"
                   value="${emailValue}">
            <button type="button" id="tx-send-code-btn" class="secondary" style="white-space:nowrap">Send Code</button>
          </div>
          <div class="wfield-hint" id="tx-send-hint">Enter your @transsion.com email address, then click Send Code.</div>
        </div>
        <div class="wfield" id="tx-code-field" style="${codeHidden}">
          <label>Verification Code</label>
          <div style="display:flex;gap:8px">
            <input type="text" id="tx-code" autocomplete="off" inputmode="numeric"
                   placeholder="6-digit code" maxlength="6" style="flex:1">
            <button type="button" id="tx-login-btn" class="secondary" style="white-space:nowrap">Login &amp; Authorize</button>
          </div>
          <div class="wfield-hint">Check your inbox (expires in 5 min).</div>
        </div>
      </div>
      <div id="tx-status" class="transsion-status" style="display:none"></div>`;
  } else if (prov.needsKey) {
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
    let regionBtns = "";
    if (prov.regions && prov.regions.length) {
      const chips = prov.regions.map((r) => {
        const active = (burl === r.url) ? ' style="font-weight:600;border-color:var(--accent)"' : "";
        return `<button type="button" class="secondary region-btn" data-url="${escHtml(r.url)}"${active}>${escHtml(r.label)}</button>`;
      }).join("");
      regionBtns = `<div style="display:flex;gap:6px;margin-bottom:8px">${chips}</div>`;
    }
    keyHtml += `
      <div class="wfield">
        <label>${escHtml(prov.baseUrlLabel)}</label>
        ${regionBtns}
        <input type="text" id="wiz-baseurl" placeholder="${escHtml(prov.defaultBaseUrl)}"
               value="${escHtml(burl)}">
        <div class="wfield-hint">Leave as-is unless you're using a proxy or custom endpoint.</div>
      </div>`;
  }

  if (prov.authFlow !== "email_code") {
    keyHtml += `
      <div style="margin-top:14px">
        <button type="button" id="wiz-test-btn" class="secondary">Test Connection</button>
        <div id="wiz-test-steps"></div>
      </div>`;
  }
  keyHtml += `</div>`;

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

  els.wizardBody.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      wizard.provider = radio.value;
      const p2 = providerById(wizard.provider);
      wizard.model   = p2.defaultModel;
      wizard.baseUrl = p2.defaultBaseUrl || "";
      if (p2.id !== "transsion") _txCodeRequested = false;
      renderModelTab();
    });
  });
  els.wizardBody.querySelectorAll(".provider-card").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector("input[type=radio]");
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
    });
  });

  const keyEl  = document.getElementById("wiz-apikey");
  const burlEl = document.getElementById("wiz-baseurl");
  if (keyEl)  keyEl.addEventListener("input",  () => { wizard.apiKey  = keyEl.value.trim(); });
  if (burlEl) burlEl.addEventListener("input", () => { wizard.baseUrl = burlEl.value.trim(); });

  els.wizardBody.querySelectorAll(".region-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const url = btn.dataset.url;
      wizard.baseUrl = url;
      if (burlEl) burlEl.value = url;
      // update active highlight
      els.wizardBody.querySelectorAll(".region-btn").forEach((b) => {
        b.style.fontWeight = "";
        b.style.borderColor = "";
      });
      btn.style.fontWeight = "600";
      btn.style.borderColor = "var(--accent)";
    });
  });

  // ── Transsion email-code login handlers ─────────────────────────────────

  // Re-login / Cancel re-login
  const txReloginBtn = document.getElementById("tx-relogin-btn");
  const txCancelBtn  = document.getElementById("tx-cancel-relogin-btn");
  if (txReloginBtn) {
    txReloginBtn.addEventListener("click", () => {
      _txShowRelogin   = true;
      _txCodeRequested = false;
      renderModelTab();
    });
  }
  if (txCancelBtn) {
    txCancelBtn.addEventListener("click", () => {
      _txShowRelogin   = false;
      _txCodeRequested = false;
      renderModelTab();
    });
  }

  const txSendBtn  = document.getElementById("tx-send-code-btn");
  const txLoginBtn = document.getElementById("tx-login-btn");
  if (txSendBtn) {
    txSendBtn.addEventListener("click", async () => {
      const email = (document.getElementById("tx-email")?.value || "").trim();
      if (!email) { _txStatus("Enter your email address first.", "error"); return; }
      txSendBtn.disabled = true;
      txSendBtn.textContent = "Sending…";
      const hint = document.getElementById("tx-send-hint");
      if (hint) hint.textContent = "Sending verification code…";
      try {
        const wsPort = Number(location.port || 8765);
        const resp = await fetch(
          `${location.protocol}//${location.hostname}:${wsPort + 1}/api/auth/send-email-code`,
          {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ email }),
          }
        );
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err?.error || `Server error ${resp.status}`);
        }
        handleTransssionCodeSent({ email });
      } catch (err) {
        txSendBtn.disabled = false;
        txSendBtn.textContent = "Send Code";
        _txStatus(`Failed to send code: ${err.message}`, "error");
      }
    });
  }
  if (txLoginBtn) {
    txLoginBtn.addEventListener("click", async () => {
      const email = (document.getElementById("tx-email")?.value || "").trim();
      const code  = (document.getElementById("tx-code")?.value  || "").trim();
      if (!email || !code) { _txStatus("Enter both email and verification code.", "error"); return; }
      txLoginBtn.disabled = true;
      txLoginBtn.textContent = "Logging in…";
      _txStatus("Authenticating…", "info");
      try {
        const wsPort = Number(location.port || 8765);
        const resp = await fetch(
          `${location.protocol}//${location.hostname}:${wsPort + 1}/api/auth/login`,
          {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ email, code }),
          }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          throw new Error(data?.error || `Server error ${resp.status}`);
        }
        handleTransssionAuthed(data);
      } catch (err) {
        txLoginBtn.disabled = false;
        txLoginBtn.textContent = "Login & Authorize";
        _txStatus(`Login failed: ${err.message}`, "error");
      }
    });
  }

  const testBtn = document.getElementById("wiz-test-btn");
  if (testBtn) {
    testBtn.addEventListener("click", () => {
      clearTimeout(_testTimer);
      _stopSpinner();
      testBtn.disabled = true;
      testBtn.textContent = "Testing…";
      const stepsEl = document.getElementById("wiz-test-steps");
      if (stepsEl) stepsEl.innerHTML = "";
      _testTimer = setTimeout(() => {
        _testTimer = null;
        _stopSpinner();
        const btn = document.getElementById("wiz-test-btn");
        if (btn) { btn.disabled = false; btn.textContent = "Test Connection"; }
        const c = document.getElementById("wiz-test-steps");
        if (c) {
          const s = document.createElement("div");
          s.className = "test-step-summary error";
          s.textContent = "✗ Timed out (30 s). Check your API key and endpoint.";
          c.appendChild(s);
        }
      }, 30000);
      send({ type: "test_provider", provider: wizard.provider, api_key: wizard.apiKey, base_url: wizard.baseUrl, model: wizard.model });
    });
  }

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

  // Transsion: empty wizard.api_key + server still falls back to disk in list_models —
  // avoid listing while user is only in email/OTP flow (before Login sets wizard.apiKey).
  const savedTranssionReady =
    sc &&
    sc.provider === "transsion" &&
    sc.api_key_set &&
    sc.transsion &&
    sc.transsion.authed;
  const skipListModels =
    prov.authFlow === "email_code" &&
    !wizard.apiKey &&
    (_txCodeRequested || !savedTranssionReady);

  const loadingEl = document.getElementById("wiz-model-loading");
  if (state.ws && state.ws.readyState === WebSocket.OPEN && !skipListModels) {
    state.ws.send(JSON.stringify({
      type: "list_models", provider: wizard.provider,
      api_key: wizard.apiKey, base_url: wizard.baseUrl || prov.defaultBaseUrl,
    }));
  } else {
    loadingEl?.remove();
  }
}

export function handleModelsResponse(msg) {
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

export function renderChannelsTab() {
  els.wizardBody.innerHTML = `<div class="conn-panel">` +
    CHANNELS.map((ch) => {
      const c          = connectors[ch.id];
      const on         = c.enabled;
      const configured = _isConfigured(ch.id, c);
      const badge      = (!on && configured)
        ? `<span class="conn-configured-badge" title="Previously configured — click toggle to re-enable">configured</span>`
        : "";
      return `
        <div class="conn-section" id="conn-${ch.id}">
          <div class="conn-section-header">
            <span class="conn-platform-icon">${ch.icon}</span>
            <div class="conn-platform-info">
              <span class="conn-platform-name">${ch.name}</span>
              <span class="conn-platform-desc">${ch.desc}</span>
            </div>
            ${badge}
            <label class="toggle-switch" title="${on ? "Enabled" : "Disabled"}">
              <input type="checkbox" id="${ch.id}-enabled" ${on ? "checked" : ""}
                     data-chan="${ch.id}">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="conn-fields" id="${ch.id}-fields"${on ? "" : ' style="display:none"'}>
            ${ch.fields(c)}
            <div class="wfield-hint" style="margin-top:4px">
              Setup guide: <a href="${ch.setupUrl}" target="_blank" rel="noopener">${ch.setupLabel} ↗</a>
            </div>
          </div>
        </div>`;
    }).join("") +
    `</div>`;

  CHANNELS.forEach(({ id }) => {
    document.getElementById(`${id}-enabled`).addEventListener("change", (e) => {
      document.getElementById(`${id}-fields`).style.display = e.target.checked ? "" : "none";
    });
  });

  // Let registered plugins append their own widget cards (e.g. Transsion community status).
  const connPanel = els.wizardBody.querySelector(".conn-panel");
  if (connPanel) _settingsWidgets.forEach((fn) => { try { fn(connPanel); } catch { /* ignore */ } });
}

// ── System tab ─────────────────────────────────────────────────────────────

export function renderSystemTab() {
  const themeMode  = wizard.themeMode || getThemeMode();
  const themeName  = wizard.theme     || getTheme();
  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Generation</h3>
      <div class="wfield">
        <label>Max output tokens</label>
        <input type="number" id="sys-max-tokens" min="0" max="32768" step="256"
               value="${escHtml(String(wizard.maxTokens))}">
        <div class="wfield-hint">Maximum tokens the model generates per response. Set 0 to remove app-side cap (provider default still applies).</div>
      </div>
      <div class="wfield">
        <label>Max tool rounds</label>
        <input type="number" id="sys-max-tool-rounds" min="0" max="1000" step="1"
               value="${escHtml(String(wizard.maxToolRounds))}">
        <div class="wfield-hint">Maximum tool calls per agent turn before forcing a final response. Set 0 for no app-side limit.</div>
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
      <h3 class="settings-section-h">Appearance</h3>
      <div class="wfield">
        <label>Theme</label>
        <div class="theme-picker" role="group" aria-label="Color theme">
          ${THEMES.map(t => `
            <button class="theme-swatch${t === themeName ? " active" : ""}"
                    data-theme-pick="${t}"
                    title="${THEME_LABELS[t] || t}"
                    type="button">
              <span class="theme-swatch-dot theme-swatch-dot--${t}"></span>
              <span class="theme-swatch-label">${THEME_LABELS[t] || t}</span>
            </button>`).join("")}
        </div>
      </div>
      <div class="wfield">
        <label>Mode</label>
        <p class="wdesc" style="margin:0 0 6px">Auto follows your OS appearance setting.</p>
        <div class="theme-mode-group" role="radiogroup" aria-label="Theme mode">
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="auto" ${themeMode === "auto" ? "checked" : ""}>
            <span>Auto (System)</span>
          </label>
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="light" ${themeMode === "light" ? "checked" : ""}>
            <span>Light</span>
          </label>
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="dark" ${themeMode === "dark" ? "checked" : ""}>
            <span>Dark</span>
          </label>
        </div>
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
      <h3 class="settings-section-h">Updates</h3>
      <p class="wdesc">Check GitHub releases and upgrade after your confirmation.</p>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Auto-check for updates</span>
          <span class="connector-desc">Background check based on interval</span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="upd-auto-check" ${wizard.updateAutoCheckEnabled ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>
      <div class="wfield" style="margin-top:8px">
        <label>Check interval (hours)</label>
        <input type="number" id="upd-interval-hours" min="1" max="168" step="1"
               value="${escHtml(String(wizard.updateCheckIntervalHours || 24))}">
      </div>
      <div class="wfield">
        <label>Channel</label>
        <select id="upd-channel">
          <option value="stable" ${wizard.updateChannel === "stable" ? "selected" : ""}>stable</option>
          <option value="prerelease" ${wizard.updateChannel === "prerelease" ? "selected" : ""}>prerelease</option>
        </select>
      </div>
      <div id="upd-status" class="wfield-hint" style="margin-top:6px"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <button type="button" id="upd-check-btn" class="secondary">Check now</button>
        <button type="button" id="upd-upgrade-btn" class="secondary">Upgrade now</button>
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
                  text-decoration:none;font-size:12px;color:var(--accent)">
          OpenRouter Key Settings ↗
        </a>
        <a href="https://platform.openai.com/usage" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          OpenAI Usage ↗
        </a>
        <a href="https://console.anthropic.com" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
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
        <div class="connector-row" style="margin-top:10px">
          <div class="connector-meta">
            <span class="connector-name">Use My Chrome</span>
            <span class="connector-desc">
              Connect HushClaw to your real Google Chrome over the Chrome DevTools Protocol (CDP).
              Uses your normal Chrome profile (cookies and logins) when the app starts Chrome with
              <code>--remote-debugging-port=9222</code>. Often works better than automation-only
              browsers for sites that block scripted logins; some sites may still restrict control.
            </span>
          </div>
          <label class="toggle">
            <input type="checkbox" id="br-use-user-chrome" ${browser.use_user_chrome ? 'checked' : ''}
                   onchange="document.getElementById('br-cdp-url-row').style.display=this.checked?'':'none'">
            <span class="slider"></span>
          </label>
        </div>
        <div id="br-cdp-url-row" class="wfield" style="margin-top:8px;${browser.use_user_chrome ? '' : 'display:none'}">
          <label>Chrome Debugging URL</label>
          <input type="text" id="br-cdp-url"
                 placeholder="http://localhost:9222"
                 value="${escHtml(browser.remote_debugging_url || 'http://localhost:9222')}">
          <div class="wfield-hint">
            Default <code>http://localhost:9222</code> — only change if you use a custom port.
            After you save settings, the <strong>first browser tool</strong> in a session connects
            here automatically (no need to type a command).
          </div>
          <details class="browser-cdp-guide">
            <summary>Step-by-step: connect your Chrome</summary>
            <ol class="browser-cdp-guide-steps">
              <li>
                Leave the URL as <code>http://localhost:9222</code> unless you deliberately run
                Chrome with another debugging port.
              </li>
              <li>
                <strong>Save</strong> these settings. Restart HushClaw if the app says a restart is required.
              </li>
              <li>
                <strong>Quit Chrome fully</strong> before the first connection
                (macOS: <kbd>Cmd</kbd>+<kbd>Q</kbd> on Chrome;
                Windows: close all windows and use &quot;Exit&quot; from the Chrome tray icon if it stays running).
                That releases the profile lock so HushClaw can start Chrome with debugging enabled while still using
                your <strong>default profile</strong> (same bookmarks, extensions, and saved logins as everyday use).
              </li>
              <li>
                Use the assistant as usual. The first time a browser tool runs, HushClaw tries to connect to that URL.
                If nothing is listening yet, it waits up to <strong>about 90 seconds</strong> for Chrome to finish
                quitting, then starts Chrome with <code>--remote-debugging-port=9222</code>.
                If you already started Chrome yourself with that flag, it connects immediately instead.
              </li>
              <li>
                Sign in on the site you need inside that Chrome window if prompted; then run browser actions again.
              </li>
              <li>
                <strong>Privacy:</strong> while remote debugging is on, other software on <em>this computer</em> could
                attach to the browser. Use only on a machine you trust.
              </li>
            </ol>
            <p class="browser-cdp-guide-foot wfield-hint">
              Official APIs and tokens (where a platform offers them) are still the most dependable for automation;
              use this mode when you need a real logged-in browser session.
            </p>
          </details>
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Skills Directories</h3>
      <div class="wfield">
        <label>Custom Skills Directory <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="sys-user-skill-dir"
               placeholder="e.g. ~/my-skills"
               value="${escHtml(wizard.userSkillDir || '')}">
        <div class="wfield-hint">
          Your own or third-party skills installed here.<br>
          System skills (managed by install.sh): <code>${escHtml(wizard.systemSkillDir || "not configured")}</code>
        </div>
      </div>
      <div class="wfield">
        <label>Workspace Directory <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="sys-workspace-dir"
               placeholder="Auto: .hushclaw/ in cwd"
               value="${escHtml(wizard.workspaceDir || '')}">
        <div class="wfield-hint">
          Per-project workspace. HushClaw reads <code>SOUL.md</code> (agent identity) and <code>USER.md</code> (user notes) from here.
          Auto-detected when a <code>.hushclaw/</code> folder exists in the current directory.
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Tool Profile</h3>
      <div class="wfield">
        <label>Profile preset</label>
        <select id="sys-tools-profile">
          <option value=""       ${wizard.toolsProfile === ""         ? "selected" : ""}>— Default (use enabled list) —</option>
          <option value="full"   ${wizard.toolsProfile === "full"     ? "selected" : ""}>full — all built-in tools</option>
          <option value="coding" ${wizard.toolsProfile === "coding"   ? "selected" : ""}>coding — file ops, shell, memory, todos</option>
          <option value="messaging" ${wizard.toolsProfile === "messaging" ? "selected" : ""}>messaging — email, calendar, memory</option>
          <option value="minimal"   ${wizard.toolsProfile === "minimal"   ? "selected" : ""}>minimal — remember, recall, get_time only</option>
        </select>
        <div class="wfield-hint">
          Restricts the tool set to a predefined profile. Applied before the enabled-list filter.
          Leave blank to rely solely on the <code>tools.enabled</code> list in your config.
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Skill API Keys</h3>
      <p class="wdesc">API keys used by installed skills. Stored in your local config file — never sent to any server except the service you configure.</p>
      <div class="wfield">
        <label>ScrapeCreators API Key
          <span class="wfield-optional"> — <a href="https://scrapecreators.com" target="_blank" rel="noopener" style="color:var(--accent)">Get free key ↗</a></span>
        </label>
        <input type="password" id="sk-scrape-creators" autocomplete="off"
               placeholder="${(wizard.apiKeys || {}).scrape_creators ? '● set' : 'paste key here'}"
               value="">
        <div class="wfield-hint">Used by the <code>tiktok-insight</code> skill (tiktok_search_videos, tiktok_get_video_info, tiktok_get_video_comments).</div>
      </div>
      <div class="wfield">
        <label>TikTok Research API Key
          <span class="wfield-optional"> — <a href="https://developers.tiktok.com/products/research-api/" target="_blank" rel="noopener" style="color:var(--accent)">Apply ↗</a></span>
        </label>
        <input type="password" id="sk-tiktok-client-key" autocomplete="off"
               placeholder="${(wizard.apiKeys || {}).tiktok_client_key ? '● set' : 'paste key here'}"
               value="">
        <div class="wfield-hint">Used by the <code>hushclaw-skill-social-insights</code> skill (tiktok_search). Set together with the secret below.</div>
      </div>
      <div class="wfield">
        <label>TikTok Research API Secret</label>
        <input type="password" id="sk-tiktok-client-secret" autocomplete="off"
               placeholder="${(wizard.apiKeys || {}).tiktok_client_secret ? '● set' : 'paste secret here'}"
               value="">
      </div>
    </div>
  `;
  bindThemeControls(els.wizardBody);
  bindThemeSwatches(els.wizardBody);
  const checkBtn = document.getElementById("upd-check-btn");
  const upgradeBtn = document.getElementById("upd-upgrade-btn");
  if (checkBtn) {
    checkBtn.addEventListener("click", () => {
      syncFormToState();
      requestCheckUpdate(true);
    });
  }
  if (upgradeBtn) {
    upgradeBtn.addEventListener("click", () => {
      syncFormToState();
      requestRunUpdate();
    });
  }
  refreshUpdateUi();
}

// ── Memory tab ─────────────────────────────────────────────────────────────

export function renderMemoryTab() {
  const ws = wizard.workspaceStatus || {};
  const wsConfigured = ws.configured;
  const wsPath = ws.path || wizard.workspaceDir || "";
  const soulOk = ws.soul_md;
  const userOk = ws.user_md;

  const wsStatusBadge = wsConfigured
    ? `<span style="color:var(--green,#4caf50);font-weight:600">✓ Active</span>`
    : `<span style="color:var(--yellow,#ff9800);font-weight:600">⚠ Not initialized</span>`;
  const soulBadge = soulOk ? `<span style="color:var(--green,#4caf50)">✓ SOUL.md</span>` : `<span style="color:var(--muted,#888)">✗ SOUL.md missing</span>`;
  const userBadge = userOk ? `<span style="color:var(--green,#4caf50)">✓ USER.md</span>` : `<span style="color:var(--muted,#888)">✗ USER.md missing</span>`;

  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Workspace &amp; Memory Files</h3>
      <p class="wdesc">
        The workspace directory holds <code>SOUL.md</code> (agent identity, injected into every session)
        and <code>USER.md</code> (user notes, auto-updated after each turn).
        Setting this up is the fastest way to prevent HushClaw from "starting from scratch".
      </p>
      <div class="wfield">
        <label>Status: ${wsStatusBadge} &nbsp; ${soulBadge} &nbsp; ${userBadge}</label>
        <div class="wfield-hint" style="margin-top:4px">
          Active path: <code>${escHtml(wsPath || "(default: ~/.hushclaw/workspace or .hushclaw/ in cwd)")}</code>
        </div>
      </div>
      <div class="wfield">
        <label>Workspace Directory <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="mem-workspace-dir"
               placeholder="Leave blank to use default (~/.hushclaw/workspace)"
               value="${escHtml(wizard.workspaceDir || '')}">
        <div class="wfield-hint">
          Override the workspace path. Leave blank to use the global default.<br>
          HushClaw auto-detects <code>.hushclaw/</code> in the current directory first.
        </div>
      </div>
      ${!wsConfigured || !soulOk || !userOk ? `
      <div class="wfield">
        <button id="mem-init-workspace-btn" class="btn-secondary" style="margin-top:4px">
          🗂 Initialize Workspace (create SOUL.md &amp; USER.md)
        </button>
        <div id="mem-init-ws-status" class="wfield-hint" style="margin-top:4px"></div>
      </div>` : `
      <div class="wfield">
        <button id="mem-init-workspace-btn" class="btn-secondary" style="margin-top:4px">
          🔄 Re-seed missing files
        </button>
        <div id="mem-init-ws-status" class="wfield-hint" style="margin-top:4px"></div>
      </div>`}
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Context &amp; Compaction</h3>
      <p class="wdesc">Controls how much conversation history is kept in context and when old turns are archived.</p>
      <div class="wfield">
        <label>History budget (tokens)</label>
        <input type="number" id="mem-history-budget" min="0" max="200000" step="1000"
               value="${escHtml(String(wizard.historyBudget))}">
        <div class="wfield-hint">Maximum tokens of conversation history kept in context before compaction triggers. Set 0 to disable compaction by budget.</div>
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
        <input type="number" id="mem-max-tokens" min="0" max="8000" step="100"
               value="${escHtml(String(wizard.memoryMaxTokens))}">
        <div class="wfield-hint">Hard cap on tokens spent on injected memories per request. Set 0 for no app-side cap.</div>
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

  // Bind init-workspace button
  const initBtn = document.getElementById("mem-init-workspace-btn");
  if (initBtn) {
    initBtn.addEventListener("click", () => {
      const pathEl = document.getElementById("mem-workspace-dir");
      const customPath = pathEl ? pathEl.value.trim() : "";
      const statusEl = document.getElementById("mem-init-ws-status");
      if (statusEl) statusEl.textContent = "Initializing…";
      initBtn.disabled = true;
      send({ type: "init_workspace", path: customPath });
    });
  }
}

// ── Integrations tab ───────────────────────────────────────────────────────

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

export function renderIntegrationsTab() {
  const pwdPlaceholder    = emailCfg.password_set    ? "••••••••  (already set)" : "App password";
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

  document.querySelectorAll("[data-cal-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = CALDAV_PROVIDERS[parseInt(btn.dataset.calPreset)];
      if (!p) return;
      document.getElementById("calendar-url").value = p.url;
    });
  });
}

// ── Settings save ──────────────────────────────────────────────────────────

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
    c.allowlist           = _fv("fs-allowlist");
    c.stream              = _fc("fs-stream", c.stream);
    c.markdown            = _fc("fs-markdown", c.markdown);
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
    c.markdown        = _fc("dc-markdown", c.markdown);
  }
  if (document.getElementById("slack-enabled")) {
    const c = connectors.slack;
    c.enabled    = _fc("slack-enabled", c.enabled);
    c.bot_token  = _fv("sl-bot-token");
    c.app_token  = _fv("sl-app-token");
    c.agent      = _fv("sl-agent") || "default";
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
    c.allowlist   = _fv("wc-allowlist");
    c.markdown    = _fc("wc-markdown", c.markdown);
  }

  const maxTokEl    = document.getElementById("sys-max-tokens");
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

  // Skill API Keys — only update keys that were actually typed into the form
  const skApiKeyFields = [
    ["sk-scrape-creators",    "scrape_creators"],
    ["sk-tiktok-client-key",  "tiktok_client_key"],
    ["sk-tiktok-client-secret","tiktok_client_secret"],
  ];
  if (!wizard.apiKeys) wizard.apiKeys = {};
  for (const [elId, key] of skApiKeyFields) {
    const el = document.getElementById(elId);
    if (el && el.value.trim() !== "") {
      wizard.apiKeys[key] = el.value.trim();
    }
    // blank value = leave unchanged (don't clear existing key unless user explicitly cleared)
  }

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
    allowlist: _intList(_al(dc.allowlist)),
    guild_allowlist: _intList(_al(dc.guild_allowlist)),
    require_mention: dc.require_mention, stream: dc.stream,
    markdown: dc.markdown !== false,
  };
  if (dc.bot_token) dcConfig.bot_token = dc.bot_token;

  const sl = connectors.slack;
  const slConfig = {
    enabled: sl.enabled, agent: sl.agent || "default",
    allowlist: _strList(_al(sl.allowlist)), stream: sl.stream,
    markdown: sl.markdown !== false,
  };
  if (sl.bot_token) slConfig.bot_token = sl.bot_token;
  if (sl.app_token) slConfig.app_token = sl.app_token;

  const dt = connectors.dingtalk;
  const dtConfig = {
    enabled: dt.enabled, agent: dt.agent || "default",
    allowlist: _strList(_al(dt.allowlist)), stream: dt.stream,
    markdown: dt.markdown !== false,
  };
  if (dt.client_id)     dtConfig.client_id     = dt.client_id;
  if (dt.client_secret) dtConfig.client_secret = dt.client_secret;

  const wc = connectors.wecom;
  const wcConfig = {
    enabled: wc.enabled, agent: wc.agent || "default",
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
      ...(calendarCfg.password ? { password: calendarCfg.password } : {}),
    },
  };
  if (wizard.apiKey && (prov.needsKey || wizard.provider === "transsion")) {
    config.provider.api_key = wizard.apiKey;
  }
  if (wizard.provider === "transsion" && _txEmail) {
    config.transsion = {
      email:        _txEmail,
      display_name: _txDisplayName || "",
    };
    if (_txAccessToken) {
      config.transsion.access_token = _txAccessToken;
    }
  }
  if (wizard.systemPrompt.trim())     config.agent.system_prompt = wizard.systemPrompt.trim();
  // Always send workspace_dir so the user can clear it (empty string = use default)
  config.agent = config.agent || {};
  config.agent.workspace_dir = wizard.workspaceDir || "";
  if (wizard.costIn  > 0) config.provider.cost_per_1k_input_tokens  = wizard.costIn;
  if (wizard.costOut > 0) config.provider.cost_per_1k_output_tokens = wizard.costOut;
  config.tools = {
    user_skill_dir: wizard.userSkillDir || "",
    profile:        wizard.toolsProfile || "",
  };
  // Skill API keys: only send keys that have values (non-empty strings)
  if (wizard.apiKeys && Object.keys(wizard.apiKeys).length > 0) {
    config.api_keys = Object.fromEntries(
      Object.entries(wizard.apiKeys).filter(([, v]) => typeof v === "string")
    );
  }

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
