"""System prompt config hygiene helpers."""
from __future__ import annotations

from hushclaw.prompts import build_system_prompt


_LEGACY_DEFAULT_MARKERS = (
    "memory lookup is not the default first step",
    "Do NOT call recall() for short operational requests",
    "Treat recall() as a targeted supplemental search",
    "Skill bodies are an exception",
    "remember_skill saves to the correct user skill directory",
    "new writes should use relative paths",
)


def is_builtin_system_prompt(text: str) -> bool:
    """Return True when *text* is the current built-in default prompt."""
    return (text or "").strip() == build_system_prompt().strip()


def is_legacy_default_system_prompt(text: str) -> bool:
    """Return True for historical default prompts accidentally persisted to TOML."""
    value = (text or "").strip()
    if len(value) < 1200:
        return False
    marker_hits = sum(1 for marker in _LEGACY_DEFAULT_MARKERS if marker in value)
    return marker_hits >= 2


def should_reset_persisted_system_prompt(text: str) -> bool:
    """Whether a persisted system_prompt should be reset to the built-in default."""
    return is_builtin_system_prompt(text) or is_legacy_default_system_prompt(text)
