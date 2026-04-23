"""Configuration dataclasses — no pydantic, no attrs."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from hushclaw.exceptions import ConfigError
from hushclaw.prompts import build_system_prompt


def _check_fraction(name: str, val: float) -> None:
    """Raise ConfigError if val is not in [0.0, 1.0]."""
    if not (0.0 <= val <= 1.0):
        raise ConfigError(f"{name} must be in [0.0, 1.0], got {val}")


@dataclass
class AgentDefinition:
    name: str
    description: str = ""
    model: str = ""          # empty = inherit global agent.model
    system_prompt: str = ""  # empty = inherit global agent.system_prompt
    tools: list[str] = field(default_factory=list)  # empty = use global tools.enabled
    # Hierarchy metadata (optional, runtime-safe defaults)
    role: str = "specialist"  # commander | specialist
    team: str = ""
    reports_to: str = ""  # parent agent name
    capabilities: list[str] = field(default_factory=list)
    # ── Job-role semantics ──────────────────────────────────────────────────
    # mode: who is allowed to invoke this agent
    #   interactive     — CLI / HTTP only
    #   autonomous      — scheduler-triggered only
    #   external_channel — connector-triggered only
    #   channel_entry   — dispatcher; accepts any source
    #   hybrid          — unrestricted (default for backward-compat)
    mode: str = "hybrid"
    # entry_policy: explicit allowlist of sources (overrides mode when non-empty)
    # values: "cli" | "scheduler" | "agent" | "telegram" | "feishu" |
    #         "discord" | "slack" | "dingtalk" | "wecom"
    entry_policy: list[str] = field(default_factory=list)
    # max_delegation_depth: how many levels deep this agent can be delegated to
    #   -1 = unlimited, 0 = cannot be a delegation target, N = max depth
    max_delegation_depth: int = -1
    # memory_policy: default scope for notes this agent writes
    #   private   → "agent:{name}"
    #   workspace → "workspace:{ws}"  (current behavior)
    #   global    → "global"
    memory_policy: str = "workspace"
    # approval_policy: gate on dangerous tool execution
    #   safe_auto        — current _confirm_fn behavior
    #   manager_approval — Phase 4: pause + write approval_request (placeholder)
    #   human_approval   — interactive REPL prompt
    approval_policy: str = "safe_auto"


@dataclass
class GatewayConfig:
    agents: list[AgentDefinition] = field(default_factory=list)
    shared_memory: bool = True
    max_concurrent_per_agent: int = 10
    pipelines: dict[str, list[str]] = field(default_factory=dict)  # name → [agent, agent, ...]
    session_ttl_hours: int = 24  # AgentLoop sessions older than this are GC'd
    # Scheduler session strategy: "job" = stable per task, "run" = new session each run
    scheduled_session_mode: str = "job"
    # Session list shaping defaults for WebUI
    session_list_limit: int = 200
    session_list_idle_days: int = 0
    session_list_hide_scheduled: bool = False


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    max_connections: int = 100
    heartbeat_interval: int = 30
    api_key: str = ""           # non-empty = require X-API-Key header
    upload_dir: Path | None = None   # None = data_dir/uploads (resolved in load_config)
    max_upload_mb: int = 20          # max file size for PUT /upload
    # Optional public base URL used to compose absolute download links.
    # Example: "https://example.com". Empty means return relative /files/... URLs only.
    public_base_url: str = ""


@dataclass
class UpdateConfig:
    auto_check_enabled: bool = True
    check_interval_hours: int = 24
    channel: str = "stable"   # "stable" | "prerelease"
    # Runtime hint persisted by UI; server may still keep in-memory state.
    last_checked_at: int = 0
    check_timeout_seconds: int = 8
    cache_ttl_seconds: int = 900
    upgrade_timeout_seconds: int = 900


@dataclass
class AgentConfig:
    model: str = "claude-sonnet-4-6"
    # Optional lightweight model for simple, non-tool-using requests.
    # When set, the loop uses cheap_model for the first round and falls back to
    # model if the response requests tool use or is likely incomplete.
    # Empty = always use model (no routing).
    cheap_model: str = ""
    max_tokens: int = 16384
    context_window: int = 180000
    max_tool_rounds: int = 40
    system_prompt: str = field(default_factory=build_system_prompt)
    # Static instructions injected into the stable (cacheable) prefix.
    # Empty = read from workspace AGENTS.md (preferred).
    # Non-empty = used as-is (overrides AGENTS.md when both exist is NOT the case;
    # AGENTS.md takes precedence — see context/engine.py DefaultContextEngine.assemble).
    instructions: str = ""
    # Memory scope for this agent. Empty = global (unscoped) recall.
    # Set automatically to the agent's name in multi-agent (Gateway) deployments.
    # E.g. "researcher" → saves/recalls "agent:researcher" scope + "global" scope.
    memory_scope: str = ""
    # Optional workspace directory. When set (or auto-detected as .hushclaw/ in cwd):
    #   AGENTS.md → injected into stable prefix (agent behavior rules; overrides instructions)
    #   SOUL.md   → injected into stable prefix (agent identity / project persona)
    #   USER.md   → injected into dynamic suffix (user notes)
    #   skills/   → highest-priority skill tier (overrides system + user skills)
    workspace_dir: Path | None = None
    # Optional directory for trajectory JSONL files (one file per session).
    # Each turn appends a record: {turn, role, content, tool_calls, tokens, ts}.
    # Empty = disabled.
    trajectory_dir: Path | None = None


@dataclass
class ProviderConfig:
    name: str = "anthropic-raw"
    api_key: str = ""
    # Credential pool for rotation on 429 / rate-limit errors.
    # When non-empty, the loop cycles through these keys before falling back to
    # exponential back-off. Strategy: fill_first (exhaust one key, then rotate).
    # api_key is always tried first; api_keys extends the pool.
    api_keys: list[str] = field(default_factory=list)
    base_url: str | None = None
    timeout: int = 120
    max_retries: int = 3          # Retry count on transient errors (0 = no retry)
    retry_base_delay: float = 1.0  # Base delay in seconds for exponential backoff
    # Token pricing in USD per 1,000 tokens (0.0 = not configured, no cost display)
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0

    def __post_init__(self):
        if self.max_retries < 0:
            raise ConfigError(f"max_retries must be >= 0, got {self.max_retries}")

    @property
    def credential_pool(self) -> list[str]:
        """Deduplicated ordered pool: primary key first, then extras."""
        seen: set[str] = set()
        pool: list[str] = []
        for key in [self.api_key] + list(self.api_keys):
            if key and key not in seen:
                seen.add(key)
                pool.append(key)
        return pool


@dataclass
class MemoryConfig:
    data_dir: Path | None = None
    max_recall_results: int = 5
    embed_provider: str = "local"  # local | ollama | openai | anthropic
    fts_weight: float = 0.6        # Hybrid search: BM25 weight
    vec_weight: float = 0.4        # Hybrid search: cosine similarity weight

    def __post_init__(self):
        _check_fraction("fts_weight", self.fts_weight)
        _check_fraction("vec_weight", self.vec_weight)


@dataclass
class KnowledgeConfig:
    """Configuration for the local document knowledge base."""
    chunk_size: int = 512        # target tokens per chunk (~4 chars/token)
    overlap: int = 64            # carry-over tokens between adjacent chunks
    max_file_bytes: int = 2 * 1024 * 1024  # 2 MB per-file hard cap


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=lambda: [
        "remember", "recall", "search_notes", "get_time", "platform_info",
        "read_file", "write_file", "list_dir", "make_download_url", "make_download_bundle",
        "run_shell",   # shell command execution (has _confirm_fn guard in REPL)
        "apply_patch", # multi-file atomic text replacement (validate-then-apply)
        "remember_skill", "list_skills", "use_skill", "install_skill", "evolve_skill",
        "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
        "add_todo", "list_todos", "complete_todo",
        # Knowledge base: local document indexing
        "index_directory", "list_indexed_docs", "refresh_index",
        # Local calendar (SQLite-backed; no external deps)
        "add_calendar_event", "list_calendar_events", "update_calendar_event", "delete_calendar_event",
        "get_day_agenda", "find_free_slots", "check_time_conflicts",
        # Web fetching (lightweight, no browser required)
        "fetch_url",   # browser-like headers + cookie jar + gzip + retry
        "jina_read",   # Jina Reader: JS-rendered clean markdown from any URL
        # Multi-agent collaboration (always registered via enable_agent_tools; listed here for visibility)
        "delegate_to_agent", "broadcast_to_agents", "run_hierarchical",
        "list_agents", "create_agent", "update_agent", "delete_agent", "spawn_agent",
        # Browser tools (active when browser.enabled = true)
        "browser_navigate", "browser_get_content", "browser_click",
        "browser_fill", "browser_submit", "browser_screenshot",
        "browser_evaluate", "browser_close",
        "browser_open_for_user", "browser_wait_for_user",
        # Accessibility snapshot (token-efficient element interaction)
        "browser_snapshot", "browser_click_ref", "browser_fill_ref",
        # Multi-tab management
        "browser_new_tab", "browser_list_tabs", "browser_focus_tab", "browser_close_tab",
        # Remote Chrome (user's already-logged-in browser)
        "browser_connect_user_chrome",
    ])
    plugin_dir: Path | None = None
    skill_dir: Path | None = None           # system: synced by install.sh
    user_skill_dir: Path | None = None      # user-configured custom skills
    timeout: int = 30
    # Skill auto-evolution: cap on auto-created SKILL.md files
    auto_skill_cap: int = 30
    # Minimum recall_count before a memory skill becomes a promotion candidate
    auto_skill_promote_threshold: int = 5
    # Tool access profile: preset subset of tools. Applied before the enabled list.
    # "" = no preset (use enabled list only); "full" | "coding" | "messaging" | "minimal"
    profile: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass
class ContextPolicyConfig:
    """Token budget configuration for the ContextEngine."""
    stable_budget: int = 4_000
    dynamic_budget: int = 4_000
    history_budget: int = 80_000
    compact_threshold: float = 0.9
    compact_keep_turns: int = 6
    compact_strategy: str = "lossless"   # "lossless" | "summarize" | "abstractive" | "prune_tool_results"
    # Memory retrieval — raised defaults: 2500 tokens gives ~4–8 meaningful memories
    # vs the old 800 which was often too small to surface relevant prior work.
    memory_min_score: float = 0.18
    memory_max_tokens: int = 2_500
    # Regex-based auto memory extraction in after_turn() (zero LLM calls)
    auto_extract: bool = True
    # Creativity engine: controlled forgetting + random recall
    # Exponential decay rate λ; score × e^(-λ × age_days). 0.0 = no decay.
    # 0.03 ≈ half-life 23 days; 0.1 ≈ half-life 7 days.
    memory_decay_rate: float = 0.0
    # Retrieval temperature. 0.0 = deterministic top-k; >0 = softmax-weighted random sampling.
    retrieval_temperature: float = 0.0
    # Fraction of memory_max_tokens to fill with random "serendipitous" memories. 0.0 = disabled.
    serendipity_budget: float = 0.0
    # Drop notes older than N days from recall pool. 0 = no hard cutoff.
    # Works alongside memory_decay_rate: decay softens scores, max_age_days is a hard gate.
    max_age_days: int = 0

    def __post_init__(self):
        _check_fraction("compact_threshold", self.compact_threshold)
        _check_fraction("memory_min_score", self.memory_min_score)
        _check_fraction("memory_decay_rate", self.memory_decay_rate)
        _check_fraction("retrieval_temperature", self.retrieval_temperature)
        _check_fraction("serendipity_budget", self.serendipity_budget)
        _valid_strategies = {"lossless", "summarize", "abstractive", "prune_tool_results"}
        if self.compact_strategy not in _valid_strategies:
            raise ConfigError(
                f"compact_strategy must be one of {sorted(_valid_strategies)}, "
                f"got {self.compact_strategy!r}"
            )


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[int] = field(default_factory=list)        # empty = everyone (DM)
    group_allowlist: list[int] = field(default_factory=list)  # empty = everyone (groups)
    group_policy: str = "allowlist"   # "open" | "allowlist" | "disabled"
    require_mention: bool = False     # require @bot_name in group messages
    polling_timeout: int = 30         # getUpdates long-poll timeout (seconds)
    markdown: bool = True             # True = send with parse_mode=HTML (converted from Markdown)


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # empty = everyone
    markdown: bool = True       # reserved (Feishu text type does not render markdown)
    encrypt_key: str = ""       # optional: message encryption key from developer console
    verification_token: str = ""  # optional: verification token from developer console


@dataclass
class DiscordConfig:
    enabled: bool = False
    bot_token: str = ""           # Bot token from Discord Developer Portal
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[int] = field(default_factory=list)        # user IDs; empty = everyone
    guild_allowlist: list[int] = field(default_factory=list)  # server IDs; empty = all guilds
    require_mention: bool = True  # require @bot_name in guild (server) channels
    stream: bool = True           # True = edit message progressively
    markdown: bool = True         # Discord auto-renders standard Markdown client-side


@dataclass
class SlackConfig:
    enabled: bool = False
    bot_token: str = ""   # xoxb-… from OAuth & Permissions
    app_token: str = ""   # xapp-… from App-Level Tokens (Socket Mode)
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # channel IDs; empty = all channels
    stream: bool = True
    markdown: bool = True  # True = send as mrkdwn blocks (Slack's Markdown variant)


@dataclass
class DingTalkConfig:
    enabled: bool = False
    client_id: str = ""      # App Key (AppKey in DingTalk Open Platform)
    client_secret: str = ""  # App Secret
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # user open IDs; empty = everyone
    stream: bool = True   # stream mode uses WebSocket — no public endpoint needed
    markdown: bool = True  # True = use sampleMarkdown message type


@dataclass
class WeChatWorkConfig:
    enabled: bool = False
    corp_id: str = ""          # Enterprise CorpID
    corp_secret: str = ""      # App Secret
    agent_id: int = 0          # App AgentID
    token: str = ""            # Callback token (for webhook verification)
    encoding_aes_key: str = "" # Optional AES key for message encryption
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # user IDs; empty = everyone
    stream: bool = False       # WeCom does not support streaming edits
    markdown: bool = True      # True = use msgtype=markdown (WeCom markdown subset)


@dataclass
class BrowserConfig:
    enabled: bool = True   # False = skip browser tool registration entirely
    headless: bool = True
    timeout: int = 30   # per-operation timeout in seconds
    persist_cookies: bool = True   # save/load storage state (cookies + localStorage) across sessions
    # Connect to user's running Chrome instead of launching a new Chromium instance.
    # Start Chrome with: chrome --remote-debugging-port=9222 --user-data-dir=<path>
    # Then set this to "http://localhost:9222". Empty = use managed Chromium.
    remote_debugging_url: str = ""


@dataclass
class EmailConfig:
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""        # App password (not account password)
    mailbox: str = "INBOX"
    use_ssl: bool = True      # IMAP over SSL
    use_tls: bool = True      # SMTP STARTTLS


@dataclass
class CalendarConfig:
    enabled: bool = False
    url: str = ""             # CalDAV service URL
    username: str = ""
    password: str = ""        # App password
    calendar_name: str = ""   # empty = all calendars
    sync_interval_minutes: int = 30  # background CalDAV pull interval
    timezone: str = ""        # IANA timezone, e.g. "Asia/Shanghai". Empty = follow browser.


@dataclass
class ConnectorsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    wecom: WeChatWorkConfig = field(default_factory=WeChatWorkConfig)


@dataclass
class TranssionConfig:
    """Persisted Transsion / TEX AI Router auth state.

    The API key (sk-xxx) itself is stored in provider.api_key.
    This section holds the login state needed to re-acquire credentials.
    """
    email: str = ""           # email used for the last successful login
    access_token: str = ""    # JWT accessToken (used to call AcquireAPICredentials)
    display_name: str = ""    # user's display name (cosmetic only)


@dataclass
class WorkspaceEntry:
    """A named workspace entry in the workspace registry."""
    name: str
    path: str
    description: str = ""


@dataclass
class WorkspacesConfig:
    """Registry of named workspaces (multi-workspace support)."""
    list: list[WorkspaceEntry] = field(default_factory=list)


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    context: ContextPolicyConfig = field(default_factory=ContextPolicyConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    connectors: ConnectorsConfig = field(default_factory=ConnectorsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    transsion: TranssionConfig = field(default_factory=TranssionConfig)
    workspaces: WorkspacesConfig = field(default_factory=WorkspacesConfig)
    # Free-form API keys for skills and integrations.
    # Stored as [api_keys] key = "value" in hushclaw.toml.
    # Skills can inject _config and read config.api_keys.get("key_name").
    api_keys: dict = field(default_factory=dict)
