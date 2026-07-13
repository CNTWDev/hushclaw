"""Deterministic per-turn execution strategy.

The model remains responsible for language understanding and tool arguments, but
the runtime should decide the broad execution envelope before calling it.  This
module intentionally uses only small, explainable signals so it is cheap,
predictable, and safe to run on every turn.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskStrategy:
    """Execution envelope selected for one user turn."""

    intent: str = "general"
    max_tool_rounds: int | None = None
    allowed_tools: frozenset[str] | None = None
    requires_tools: bool = True
    reason: str = ""

    def reflection_fingerprint(self) -> str:
        """Map the runtime intent to the existing reflection taxonomy."""
        return {
            "research": "web_research",
            "code_change": "code_change",
        }.get(self.intent, "general_assistance")


def classify_task(
    user_input: str,
    *,
    has_images: bool = False,
    has_references: bool = False,
) -> TaskStrategy:
    """Conservative fallback when the LLM classifier is unavailable.

    This fallback deliberately does not interpret language. Keeping tools
    available is safer for task completion than guessing that a short turn is
    ordinary conversation and silently disabling execution.
    """
    return TaskStrategy(reason="semantic classifier unavailable")
