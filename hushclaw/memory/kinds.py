"""Memory kind definitions and routing policy.

Keep memory layering explicit without introducing a heavy manager abstraction.
This module is the single place that decides which note kinds are:
- user-visible in the memories panel
- eligible for long-term recall injection
- treated as telemetry or session-internal artifacts
"""
from __future__ import annotations

USER_MODEL = "user_model"
PROJECT_KNOWLEDGE = "project_knowledge"
DECISION = "decision"
SESSION_MEMORY = "session_memory"
TELEMETRY = "telemetry"

ALL_MEMORY_KINDS = {
    USER_MODEL,
    PROJECT_KNOWLEDGE,
    DECISION,
    SESSION_MEMORY,
    TELEMETRY,
}

# What a user should normally browse/manage as memory.
USER_VISIBLE_MEMORY_KINDS = {
    USER_MODEL,
    PROJECT_KNOWLEDGE,
    DECISION,
}

# What should be auto-injected by recall.
RECALL_MEMORY_KINDS = {
    USER_MODEL,
    PROJECT_KNOWLEDGE,
    DECISION,
}

SYSTEM_MEMORY_TAGS = {
    "_compact_archive",
    "_compact_abstractive",
    "_skill_usage",
    "_correction",
}


def is_valid_memory_kind(value: str | None) -> bool:
    return bool(value) and value in ALL_MEMORY_KINDS


def infer_memory_kind(
    *,
    note_type: str = "fact",
    tags: list[str] | None = None,
    memory_kind: str = "",
) -> str:
    """Normalize explicit or implicit memory kind selection."""
    if is_valid_memory_kind(memory_kind):
        return str(memory_kind)

    tag_set = set(tags or [])
    if {"_compact_archive", "_compact_abstractive"} & tag_set:
        return SESSION_MEMORY
    if {"_correction", "_skill_usage"} & tag_set:
        return TELEMETRY

    if note_type in {"interest", "belief", "preference"}:
        return USER_MODEL
    if note_type == "decision":
        return DECISION
    if note_type == "action_log":
        return TELEMETRY
    return PROJECT_KNOWLEDGE
