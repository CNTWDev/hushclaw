"""InteractionGate: classify turn-level user-interaction control signals."""
from __future__ import annotations

from typing import TYPE_CHECKING
import re

if TYPE_CHECKING:
    from hushclaw.providers.base import LLMResponse


_CONFIRMATION_WITH_CHANGES_RE = re.compile(
    r"(?:补充|但是|不过|改成|别|不要|先别|instead|but|change|don't|do not)",
    re.IGNORECASE,
)

_PLAIN_CONFIRMATION_RE = re.compile(
    r"(?:确认|可以|好的|好|行|继续|按这个来|没问题|ok|okay|yes|yep|sure|go ahead|continue|confirmed)",
    re.IGNORECASE,
)


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

    @staticmethod
    def is_plain_confirmation(text: str) -> bool:
        """Return True for a short reply that simply confirms paused work."""
        normalized = " ".join((text or "").strip().split()).lower()
        if not normalized:
            return False
        if _CONFIRMATION_WITH_CHANGES_RE.search(normalized):
            return False
        return bool(re.fullmatch(_PLAIN_CONFIRMATION_RE, normalized))
