"""Default configuration values."""
from hushclaw.prompts import build_system_prompt

DEFAULTS: dict = {
    "agent": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "context_window": 180000,
        "compaction_threshold": 0.8,
        "max_tool_rounds": 30,
        "auto_remember": False,
        "system_prompt": build_system_prompt(),
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
