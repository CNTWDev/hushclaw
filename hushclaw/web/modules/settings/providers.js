/**
 * settings/providers.js — AI provider and connector channel definitions.
 */

import { escHtml } from "../state.js";

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
export function _isConfigured(platform, c) {
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="tg-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="fs-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="dc-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="sl-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
    name: "DingTalk",
    desc: "Stream mode WebSocket bot. No public endpoint needed.",
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="dt-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
    name: "WeCom",
    desc: "HTTP callback webhook. Requires a publicly accessible server URL.",
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
        <label>Workspace <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="wc-workspace" value="${escHtml(c.workspace || '')}" placeholder="default">
        <div class="wfield-hint">Named workspace to use for inbound messages. Leave blank to use the active workspace.</div>
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
