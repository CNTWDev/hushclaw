"""InteractionGate: classify turn-level user-interaction control signals."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.providers.base import LLMResponse


_CONFIRM_PATTERNS = (
    r"(?:你确认吗|请确认|确认后|等你确认|是否确认|可以吗|要继续吗|"
    r"有什么想补充|想补充的方向|需要补充|你看这样可以吗|"
    r"明白了吗|如果确认|确认 OK|确认OK)",
    r"(?:please confirm|confirm before|once you confirm|waiting for your confirmation|"
    r"do you confirm|is that okay|does that look right|anything to add|"
    r"any direction to add|before I continue)",
)

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
        """Return True when assistant-visible text should hand control to the user."""
        normalized = " ".join((text or "").split())
        if not normalized:
            return False
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _CONFIRM_PATTERNS)

    @staticmethod
    def should_pause_before_tools(response: "LLMResponse", visible_text: str = "") -> bool:
        """Return True when a tool-use response first needs user confirmation."""
        if response.stop_reason != "tool_use" or not response.tool_calls:
            return False
        return InteractionGate.asks_for_input(visible_text or response.content or "")

    @staticmethod
    def is_plain_confirmation(text: str) -> bool:
        """Return True for a short reply that simply confirms paused work."""
        normalized = " ".join((text or "").strip().split()).lower()
        if not normalized:
            return False
        if _CONFIRMATION_WITH_CHANGES_RE.search(normalized):
            return False
        return bool(re.fullmatch(_PLAIN_CONFIRMATION_RE, normalized))
