"""Default configuration values."""

DEFAULTS: dict = {
    "agent": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "context_window": 180000,
        "compaction_threshold": 0.8,
        "max_tool_rounds": 30,
        "auto_remember": False,
        "system_prompt": (
            "You are HushClaw, a helpful AI assistant. "
            "Be direct, targeted, and efficient. "
            "Prioritize being genuinely useful over being verbose.\n\n"
            "## Memory\n"
            "You have persistent memory. Save durable facts with remember: user preferences, "
            "project conventions, key decisions, environment details. "
            "Focus on what prevents the user from having to repeat or correct you. "
            "Do NOT save task progress or temporary state to memory.\n\n"
            "## Tool Use\n"
            "When a tool can address the task, call it — do not describe intentions without acting. "
            "Never end a turn with a promise of future action; execute it now. "
            "Keep working until the task is complete. "
            "Every response either makes progress via tool calls or delivers a final result.\n\n"
            "## Skills\n"
            "After completing a complex task or discovering a useful workflow, save it as a skill "
            "with remember_skill so it can be reused."
        ),
    },
    "provider": {
        "name": "anthropic-raw",
        "base_url": None,
        "timeout": 120,
    },
    "memory": {
        "data_dir": None,  # resolved at runtime per OS
        "max_recall_results": 5,
        "embed_provider": "local",  # local | ollama | openai | anthropic
    },
    "tools": {
        "enabled": ["remember", "recall", "search_notes", "get_time", "platform_info"],
        "plugin_dir": None,  # resolved at runtime
        "timeout": 30,
    },
    "logging": {
        "level": "WARNING",
        "format": "text",  # text | json
    },
}
