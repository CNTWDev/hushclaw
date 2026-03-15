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


@dataclass
class GatewayConfig:
    agents: list[AgentDefinition] = field(default_factory=list)
    shared_memory: bool = True
    max_concurrent_per_agent: int = 10
    pipelines: dict[str, list[str]] = field(default_factory=dict)  # name → [agent, agent, ...]
    session_ttl_hours: int = 24  # AgentLoop sessions older than this are GC'd


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    max_connections: int = 100
    heartbeat_interval: int = 30
    api_key: str = ""  # non-empty = require X-API-Key header


@dataclass
class AgentConfig:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    context_window: int = 180000
    max_tool_rounds: int = 10
    system_prompt: str = (
        "You are GhostClaw, a helpful AI assistant with persistent memory. "
        "You can remember information across sessions using your memory tools."
    )
    # Static instructions injected into the stable (cacheable) prefix
    instructions: str = (
        "Before starting any complex or multi-step task, call recall_skill to check "
        "if you have a relevant skill. "
        "After successfully completing a task using a non-obvious approach, call "
        "remember_skill to save it for future use."
    )


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
        "remember_skill", "recall_skill", "list_my_skills",
        "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
        "add_todo", "list_todos", "complete_todo",
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
    skill_dir: Path | None = None
    timeout: int = 30


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass
class ContextPolicyConfig:
    """Token budget configuration for the ContextEngine."""
    stable_budget: int = 1_500
    dynamic_budget: int = 2_500
    history_budget: int = 60_000
    compact_threshold: float = 0.85
    compact_keep_turns: int = 6
    compact_strategy: str = "lossless"   # "lossless" | "summarize" | "abstractive"
    memory_min_score: float = 0.25
    memory_max_tokens: int = 800
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
    allowlist: list[int] = field(default_factory=list)        # empty = everyone
    group_allowlist: list[int] = field(default_factory=list)
    polling_timeout: int = 30   # getUpdates long-poll timeout (seconds)
    stream: bool = True         # True = editMessage to simulate streaming


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    agent: str = "default"
    allowlist: list[str] = field(default_factory=list)  # empty = everyone
    stream: bool = False        # False = safer default (patch needs Interactive Card perms)


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
class ConnectorsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)


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
    connectors: ConnectorsConfig = field(default_factory=ConnectorsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
