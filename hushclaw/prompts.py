"""
Prompt constants and assembly helpers.

All literal prompt text lives here. Nothing else in the codebase should contain
prompt strings — import the relevant constant instead.

Architecture (mirrors hermes-agent prompt_builder.py pattern):
  AGENT_IDENTITY        — who HushClaw is
  MEMORY_GUIDANCE       — what to save / not save
  TOOL_USE_GUIDANCE     — how to use tools (model-agnostic enforcement)
  SKILLS_GUIDANCE       — when to save a skill

  PLATFORM_HINTS        — per-channel formatting overrides (Telegram, Feishu, cron, …)

  COMPACT_SYSTEM            — system role for the summarisation LLM call
  COMPACT_LOSSLESS_TEMPLATE — structured handoff prompt (lossless / summarize strategies)
  COMPACT_ABSTRACTIVE_TEMPLATE — pattern-extraction prompt (abstractive strategy)
  COMPACT_SUMMARY_PREFIX    — prefix injected before a compressed context block

  SECTION_*             — markdown section headers used in context assembly

Functions:
  build_system_prompt(platform="") → assembled base system prompt
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core identity blocks
# ---------------------------------------------------------------------------

AGENT_IDENTITY: str = (
    "You are HushClaw, a helpful AI assistant. "
    "Be direct and clear. "
    "Calibrate response depth to the complexity of the request — "
    "brief for simple questions, thorough for complex ones."
)

MEMORY_GUIDANCE: str = (
    "## Memory\n"
    "You have persistent memory. Save durable facts with remember: user preferences, "
    "project conventions, key decisions, environment details. "
    "Focus on what prevents the user from having to repeat or correct you. "
    "Do NOT save task progress or temporary state to memory. "
    "Relevant memories are automatically recalled into your context before each response — "
    "call recall() only for targeted supplemental searches beyond what was auto-injected."
)

TOOL_USE_GUIDANCE: str = (
    "## Tool Use\n"
    "When a tool can address the task, call it — do not describe intentions without acting. "
    "Never end a turn with a promise of future action; execute it now. "
    "Keep working until the task is complete. "
    "Every response either makes progress via tool calls or delivers a final result."
)

SKILLS_GUIDANCE: str = (
    "## Skills\n"
    "After completing a complex task or discovering a useful workflow, save it as a skill "
    "with remember_skill so it can be reused."
)

# ---------------------------------------------------------------------------
# Per-channel platform hints
# Injected when the agent runs inside a specific connector / channel.
# The key matches the connector name used in ConnectorsConfig.
# ---------------------------------------------------------------------------

PLATFORM_HINTS: dict[str, str] = {
    "telegram": (
        "## Channel: Telegram\n"
        "Format for Telegram: use plain Markdown (bold, italic, code blocks). "
        "Keep replies concise — split long content across messages if needed. "
        "Attach files via the file tool; never paste large binary content inline."
    ),
    "feishu": (
        "## Channel: Feishu\n"
        "Format for Feishu: plain text or simple Markdown. "
        "Keep replies concise and structured."
    ),
    "discord": (
        "## Channel: Discord\n"
        "Format for Discord: use Discord Markdown (**bold**, `code`). "
        "Keep messages under 2,000 characters; split longer content."
    ),
    "whatsapp": (
        "## Channel: WhatsApp\n"
        "Plain text only — no Markdown. "
        "Keep replies concise; split long content across messages."
    ),
    "slack": (
        "## Channel: Slack\n"
        "Format for Slack: use Slack Markdown (*bold*, `code`, ```code block```). "
        "Keep replies focused."
    ),
    "cron": (
        "## Channel: Scheduled task\n"
        "You are running as a scheduled job — no user is present. "
        "Do not ask questions or request clarification. "
        "Execute the task fully and autonomously, making reasonable decisions. "
        "Your response is delivered to the configured destination; put the primary "
        "content directly in your reply."
    ),
    "cli": (
        "## Channel: CLI\n"
        "You are running in a terminal. Plain text or ANSI-compatible Markdown."
    ),
}

# ---------------------------------------------------------------------------
# Compaction prompts — consumed by DefaultContextEngine.compact()
# ---------------------------------------------------------------------------

COMPACT_SYSTEM: str = (
    "You are creating a context checkpoint for a future assistant "
    "that will continue this conversation. "
    "Output only a structured summary — no preamble, no greeting. "
    "Do NOT respond to any questions or requests in the conversation."
)

COMPACT_LOSSLESS_TEMPLATE: str = (
    "Summarise the conversation below as a structured handoff. "
    "Use exactly this format:\n\n"
    "## Goal\n"
    "## Progress\n"
    "### Done\n"
    "### In Progress\n"
    "## Key Decisions\n"
    "## Pending User Asks\n"
    "## Critical Context\n\n"
    "Keep each section brief. Include only what is needed to continue the work."
)

COMPACT_ABSTRACTIVE_TEMPLATE: str = (
    "You are compressing a conversation for long-term memory.\n"
    "Your task: Extract only the abstract PATTERNS, PRINCIPLES, and INSIGHTS.\n"
    "Rules:\n"
    "- DO NOT include specific facts, exact quotes, or proper nouns unless essential\n"
    "- DO NOT list what was discussed; describe what was LEARNED\n"
    "- Merge similar ideas into generalizations\n"
    "- Write in 3-5 bullet points maximum\n"
    "- Each bullet = one transferable principle"
)

COMPACT_SUMMARY_PREFIX: str = (
    "[Context summary — earlier turns compacted. "
    "Treat as background reference only; do not re-address work already completed. "
    "Respond only to the latest user message that follows.]"
)

# ---------------------------------------------------------------------------
# Section headers used in context assembly (engine.py)
# ---------------------------------------------------------------------------

SECTION_AGENT_INSTRUCTIONS: str = "## Agent Instructions"
SECTION_INSTRUCTIONS: str = "## Instructions"
SECTION_WORKSPACE_IDENTITY: str = "## Workspace Identity"
SECTION_USER_NOTES: str = "## Workspace User Notes"
SECTION_RECALLED_MEMORIES: str = "## Recalled memories"
SECTION_RANDOM_MEMORIES: str = "## Random memories"

# ---------------------------------------------------------------------------
# Assembly helper
# ---------------------------------------------------------------------------

def build_system_prompt(platform: str = "") -> str:
    """Return the base system prompt for the given platform.

    Canonical factory used by AgentConfig.system_prompt and defaults.py.

    Args:
        platform: Optional channel key ("telegram", "feishu", "discord",
                  "whatsapp", "slack", "cron", "cli"). Empty = no platform hint.

    Returns:
        Assembled system prompt string (no date — injected by the context engine).
    """
    parts = [AGENT_IDENTITY, MEMORY_GUIDANCE, TOOL_USE_GUIDANCE, SKILLS_GUIDANCE]
    hint = PLATFORM_HINTS.get(platform, "")
    if hint:
        parts.append(hint)
    return "\n\n".join(parts)
