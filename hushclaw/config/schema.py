"""Configuration dataclasses — no pydantic, no attrs."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


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
    max_tokens: int = 4096
    context_window: int = 180000
    max_tool_rounds: int = 40
    system_prompt: str = (
        "You are HushClaw, a helpful AI assistant with persistent memory. "
        "You can remember information across sessions using your memory tools. "
        "Today is {date}."
    )
    # Static instructions injected into the stable (cacheable) prefix
    instructions: str = (
        "## Memory-First Behavior\n"
        "At the start of every task or conversation, proactively call recall() with "
        "relevant keywords to check if you have prior context about this topic, project, "
        "or user preference. Reference recalled memories explicitly — never start from "
        "scratch when history exists.\n"
        "After completing any important task (generating a document, making a key decision, "
        "finishing a research task), call remember() to save: the outcome, the file path, "
        "key decisions made, and any user preferences expressed. Use a descriptive title "
        "so the memory can be retrieved later.\n"
        "## Skill-First Behavior\n"
        "Before starting any task that involves creating documents (PPT, Word, PDF, spreadsheet), "
        "writing code, researching, editing files, or any multi-step workflow, "
        "ALWAYS call recall_skill first. "
        "recall_skill searches both installed skill packages and your saved skills — "
        "if it returns instructions, follow them exactly. "
        "After successfully completing a task using a non-obvious approach, call "
        "remember_skill to save it for future use. "
        "When you are a commander agent with direct reports listed in your identity block, "
        "you MUST delegate work to your direct reports rather than handling everything yourself. "
        "Use delegate_to_agent for single-agent tasks, broadcast_to_agents for parallel "
        "multi-agent tasks, or run_hierarchical to dispatch to all direct reports at once. "
        "Always synthesize the subordinates' outputs into a final response. "
        "When the user asks to register or change named gateway agents, call list_agents "
        "and then update_agent or create_agent as needed; do not claim success unless "
        "those tools return success. Agents defined under [[gateway.agents]] in config "
        "cannot be updated at runtime. The update_agent tool only changes description, "
        "model, system prompt, and instructions—not which tools an agent may invoke; "
        "that requires editing configuration. For organization changes (role/team/"
        "reports_to/capabilities), always execute tools first, then summarize actual "
        "tool results; do not describe hypothetical updates. Use clear_* flags when "
        "the user asks to remove reporting lines, teams, or capabilities. "
        "IMPORTANT — Web access rules: "
        "1) For social media platforms (TikTok, Twitter/X, Instagram, LinkedIn, YouTube, "
        "Weibo, Xiaohongshu/RED, WeChat Official Accounts, Facebook, Threads, etc.) and "
        "any site that requires login or JavaScript rendering, you MUST use browser_navigate "
        "followed by browser_get_content or browser_snapshot. NEVER use fetch_url for these. "
        "2) If you receive a browser 'not authenticated' or login-wall response, call "
        "browser_open_for_user to let the user log in, then browser_wait_for_user. "
        "3) Use fetch_url only for plain public APIs, RSS feeds, or raw data endpoints "
        "that do not require a browser. "
        "4) For downloadable files produced by tools, NEVER invent absolute domains. "
        "Only return trusted relative links that start with '/files/'. "
        "If an absolute link is explicitly required, use configured public_base_url only."
    )
    # Memory scope for this agent. Empty = global (unscoped) recall.
    # Set automatically to the agent's name in multi-agent (Gateway) deployments.
    # E.g. "researcher" → saves/recalls "agent:researcher" scope + "global" scope.
    memory_scope: str = ""
    # Optional workspace directory. When set (or auto-detected as .hushclaw/ in cwd):
    #   SOUL.md → injected into stable prefix (agent identity / project persona)
    #   USER.md → injected into dynamic suffix (user notes, auto-updated by after_turn)
    #   skills/ → highest-priority skill tier (overrides system + user skills)
    workspace_dir: Path | None = None


@dataclass
class ProviderConfig:
    name: str = "anthropic-raw"
    api_key: str = ""
    base_url: str | None = None
    timeout: int = 120
    max_retries: int = 3          # Retry count on transient errors (0 = no retry)
    retry_base_delay: float = 1.0  # Base delay in seconds for exponential backoff
    # Token pricing in USD per 1,000 tokens (0.0 = not configured, no cost display)
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0


@dataclass
class MemoryConfig:
    data_dir: Path | None = None
    max_recall_results: int = 5
    embed_provider: str = "local"  # local | ollama | openai | anthropic
    fts_weight: float = 0.6        # Hybrid search: BM25 weight
    vec_weight: float = 0.4        # Hybrid search: cosine similarity weight


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=lambda: [
        "remember", "recall", "search_notes", "get_time", "platform_info",
        "read_file", "write_file", "list_dir", "make_download_url",
        "run_shell",   # shell command execution (has _confirm_fn guard in REPL)
        "apply_patch", # multi-file atomic text replacement (validate-then-apply)
        "remember_skill", "recall_skill", "list_my_skills", "promote_skill",
        "list_skills", "use_skill",
        "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
        "add_todo", "list_todos", "complete_todo",
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
    stable_budget: int = 1_500
    dynamic_budget: int = 2_500
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


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    agent: str = "default"
    allowlist: list[int] = field(default_factory=list)        # empty = everyone (DM)
    group_allowlist: list[int] = field(default_factory=list)  # empty = everyone (groups)
    group_policy: str = "allowlist"   # "open" | "allowlist" | "disabled"
    require_mention: bool = False     # require @bot_name in group messages
    polling_timeout: int = 30         # getUpdates long-poll timeout (seconds)
    stream: bool = True               # True = editMessage to simulate streaming


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    agent: str = "default"
    allowlist: list[str] = field(default_factory=list)  # empty = everyone
    stream: bool = False        # False = safer default (patch needs Interactive Card perms)
    encrypt_key: str = ""       # optional: message encryption key from developer console
    verification_token: str = ""  # optional: verification token from developer console


@dataclass
class DiscordConfig:
    enabled: bool = False
    bot_token: str = ""           # Bot token from Discord Developer Portal
    agent: str = "default"
    allowlist: list[int] = field(default_factory=list)        # user IDs; empty = everyone
    guild_allowlist: list[int] = field(default_factory=list)  # server IDs; empty = all guilds
    require_mention: bool = True  # require @bot_name in guild (server) channels
    stream: bool = True           # True = edit message progressively


@dataclass
class SlackConfig:
    enabled: bool = False
    bot_token: str = ""   # xoxb-… from OAuth & Permissions
    app_token: str = ""   # xapp-… from App-Level Tokens (Socket Mode)
    agent: str = "default"
    allowlist: list[str] = field(default_factory=list)  # channel IDs; empty = all channels
    stream: bool = True


@dataclass
class DingTalkConfig:
    enabled: bool = False
    client_id: str = ""      # App Key (AppKey in DingTalk Open Platform)
    client_secret: str = ""  # App Secret
    agent: str = "default"
    allowlist: list[str] = field(default_factory=list)  # user open IDs; empty = everyone
    stream: bool = True   # stream mode uses WebSocket — no public endpoint needed


@dataclass
class WeChatWorkConfig:
    enabled: bool = False
    corp_id: str = ""          # Enterprise CorpID
    corp_secret: str = ""      # App Secret
    agent_id: int = 0          # App AgentID
    token: str = ""            # Callback token (for webhook verification)
    encoding_aes_key: str = "" # Optional AES key for message encryption
    agent: str = "default"
    allowlist: list[str] = field(default_factory=list)  # user IDs; empty = everyone
    stream: bool = False       # WeCom does not support streaming edits


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


@dataclass
class ConnectorsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    wecom: WeChatWorkConfig = field(default_factory=WeChatWorkConfig)


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
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
