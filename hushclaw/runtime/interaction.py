"""InteractionGate: classify non-semantic interaction control signals."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.providers.base import LLMResponse


class InteractionGate:
    """Stateless classifier for user-facing turn control.

    The loop owns runtime state such as pending tool calls. This gate only
    answers whether visible text or a short user reply implies a control
    transition like awaiting user input or confirming paused work.
    """

    @staticmethod
    def asks_for_input(text: str) -> bool:
        """Natural-language text alone must not change loop control state."""
        return False

    @staticmethod
    def should_pause_before_tools(response: "LLMResponse", visible_text: str = "") -> bool:
        """Generic text-based tool pauses are disabled; use explicit tool policies."""
        return False
