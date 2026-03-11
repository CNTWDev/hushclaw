"""ContextEngine ABC and DefaultContextEngine implementation."""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING

from ghostclaw.context.policy import ContextPolicy
from ghostclaw.providers.base import Message
from ghostclaw.util.logging import get_logger
from ghostclaw.util.tokens import estimate_messages_tokens

if TYPE_CHECKING:
    from ghostclaw.config.schema import AgentConfig
    from ghostclaw.memory.store import MemoryStore
    from ghostclaw.providers.base import LLMProvider

log = get_logger("context")

# Lightweight patterns for auto-extracting facts from conversation turns.
# Only triggered from after_turn(); zero LLM calls.
_AUTO_EXTRACT_PATTERNS = [
    # Names / identities
    r"(?:我叫|名字是|my name is|I(?:'m| am) called)\s+(\S+)",
    # Project names
    r"(?:项目名|project(?:\s+name)?)\s*[：:=]\s*(.+?)(?:\s*[,\n]|$)",
    # URLs
    r"https?://[^\s\"'>]+",
    # Unix file paths (must look like /foo/bar.ext)
    r"(?<!\w)(/(?:[\w.%-]+/)+[\w.%-]+\.\w+)",
    # Version strings
    r"\bv\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?\b",
    # Key=value config lines (e.g. "API_KEY = sk-...")
    r"(?:^|\n)\s*([\w_]+)\s*=\s*(\S+)",
]


class ContextEngine(ABC):
    """
    Pluggable context lifecycle. Override any hook to customize behavior.

    Lifecycle: bootstrap → assemble → compact → after_turn
    """

    @abstractmethod
    async def assemble(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        session_id: str | None = None,
    ) -> tuple[str, str]:
        """
        Build system prompt within token budget.

        Returns (stable_prefix, dynamic_suffix):
          stable_prefix — provider-cacheable (instructions, static rules, no date)
          dynamic_suffix — per-query (today's date, relevant memories, task hint)
        """

    @abstractmethod
    async def compact(
        self,
        messages: list[Message],
        policy: ContextPolicy,
        provider: "LLMProvider",
        model: str,
        memory: "MemoryStore",
        session_id: str,
    ) -> list[Message]:
        """
        Compact context when history exceeds budget.
        Returns the new (smaller) messages list.
        """

    @abstractmethod
    async def after_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        memory: "MemoryStore",
    ) -> None:
        """Post-turn hook. Called after turn is persisted."""


class DefaultContextEngine(ContextEngine):
    """
    Token-efficient default implementation.

    - assemble(): stable prefix (role + static instructions) +
                  dynamic suffix (date + score-gated memories)
    - compact():  lossless — compress old turns to memory, replace with digest
    - after_turn(): lightweight regex-based fact extraction (no LLM calls).
                    Disable with auto_extract=False.
    """

    def __init__(self, auto_extract: bool = True) -> None:
        self.auto_extract = auto_extract

    async def assemble(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        session_id: str | None = None,
    ) -> tuple[str, str]:
        # --- Stable prefix (no date, no per-query content) ---
        base_prompt = config.system_prompt
        # Strip the {date} placeholder — it moves to dynamic suffix
        stable = base_prompt.replace(" Today is {date}.", "").replace("Today is {date}.", "")

        if config.instructions:
            stable += f"\n\n## Instructions\n{config.instructions}"

        # --- Dynamic suffix (per-query fresh content) ---
        today = date.today().isoformat()
        dynamic_parts = [f"Today is {today}."]

        # Score-gated, budget-capped memory injection (session-cached)
        memories_text = memory.recall_with_budget(
            query,
            min_score=policy.memory_min_score,
            max_tokens=policy.memory_max_tokens,
            session_id=session_id,
        )
        if memories_text:
            dynamic_parts.append(f"## Relevant memories\n{memories_text}")

        dynamic = "\n\n".join(dynamic_parts)
        return stable, dynamic

    async def compact(
        self,
        messages: list[Message],
        policy: ContextPolicy,
        provider: "LLMProvider",
        model: str,
        memory: "MemoryStore",
        session_id: str,
    ) -> list[Message]:
        keep = policy.compact_keep_turns
        if len(messages) <= keep:
            return messages

        old_messages = messages[:-keep] if keep > 0 else messages
        recent_messages = messages[-keep:] if keep > 0 else []

        if policy.compact_strategy == "lossless":
            # Archive old turns to memory before compressing
            archive_text = "\n\n".join(
                f"[{m.role}]: {m.content if isinstance(m.content, str) else str(m.content)}"
                for m in old_messages
                if isinstance(m.content, (str, list))
            )
            if archive_text:
                memory.remember(
                    archive_text,
                    title=f"Archived context for session {session_id[:8]}",
                    tags=["_compact_archive", session_id],
                )

        # Summarize old portion (1 LLM call, ~1024 output tokens)
        summary_prompt = (
            "Summarize the following conversation excerpt in concise bullet points. "
            "Focus on key facts, decisions, and context needed for continuation.\n\n"
            + "\n".join(
                f"{m.role}: {m.content if isinstance(m.content, str) else '[tool/content block]'}"
                for m in old_messages[:20]  # cap to avoid huge prompts
            )
        )
        try:
            resp = await provider.complete(
                messages=[Message(role="user", content=summary_prompt)],
                system="You summarize conversation history concisely.",
                max_tokens=1024,
                model=model,
            )
            summary = resp.content
            memory.save_session_summary(session_id, summary)
            compressed = [Message(role="user", content=f"[Compressed context]\n{summary}")]
            log.info("Context compacted: %d→%d messages", len(messages), len(compressed) + len(recent_messages))
            return compressed + recent_messages
        except Exception as e:
            log.error("Compact failed: %s — dropping oldest half instead", e)
            mid = len(messages) // 2
            return messages[mid:]

    async def after_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        memory: "MemoryStore",
    ) -> None:
        """Extract facts from the turn using lightweight regex patterns.

        Zero LLM calls. Stores up to 3 facts per turn, tagged _auto_extract.
        These have lower implicit relevance than user-saved memories.
        Disable via context.auto_extract = false in config.
        """
        if not self.auto_extract:
            return

        combined = f"{user_input}\n{assistant_response}"
        seen: set[str] = set()
        extracted: list[str] = []

        for pattern in _AUTO_EXTRACT_PATTERNS:
            for match in re.findall(pattern, combined, re.IGNORECASE | re.MULTILINE):
                if isinstance(match, tuple):
                    fact = " ".join(m.strip() for m in match if m.strip())
                else:
                    fact = match.strip()
                if fact and len(fact) >= 6 and fact not in seen:
                    seen.add(fact)
                    extracted.append(fact)

        for fact in extracted[:3]:
            try:
                memory.remember(
                    fact,
                    title=f"Auto: {fact[:60]}",
                    tags=["_auto_extract"],
                )
            except Exception as e:
                log.debug("auto_extract save failed: %s", e)


def needs_compaction(messages: list[Message], policy: ContextPolicy) -> bool:
    """Return True if the message history exceeds the compaction threshold."""
    current = estimate_messages_tokens(messages)
    threshold = int(policy.history_budget * policy.compact_threshold)
    return current >= threshold
