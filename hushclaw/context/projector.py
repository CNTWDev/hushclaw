"""Deprecated post-turn projection service.

Semantic memory extraction now lives in LearningController and uses the
configured LLM. This compatibility shim intentionally performs no extraction.
"""
from __future__ import annotations


class TurnProjectionService:
    def __init__(self, auto_extract: bool = True) -> None:
        self.auto_extract = auto_extract

    async def after_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        memory,
        source_message_id: str = "",
    ) -> None:
        return None
