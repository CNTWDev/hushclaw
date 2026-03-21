"""ContextEngine ABC and DefaultContextEngine implementation."""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING

from hushclaw.context.policy import ContextPolicy
from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger
from hushclaw.util.tokens import estimate_messages_tokens

if TYPE_CHECKING:
    from hushclaw.config.schema import AgentConfig
    from hushclaw.memory.store import MemoryStore
    from hushclaw.providers.base import LLMProvider

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

        # Determine memory scopes: if agent has a memory_scope, restrict recall
        # to ["global", "agent:{scope}"] — else query all scopes (None = unfiltered).
        ms = config.memory_scope
        recall_scopes: list[str] | None = ["global", f"agent:{ms}"] if ms else None

        # Score-gated, budget-capped memory injection (session-cached)
        serendipity = max(0.0, min(1.0, policy.serendipity_budget))
        if serendipity > 0.0:
            random_budget = int(policy.memory_max_tokens * serendipity)
            main_budget = policy.memory_max_tokens - random_budget
        else:
            main_budget = policy.memory_max_tokens
            random_budget = 0

        memories_text = memory.recall_with_budget(
            query,
            min_score=policy.memory_min_score,
            max_tokens=main_budget,
            session_id=session_id,
            decay_rate=policy.memory_decay_rate,
            retrieval_temperature=policy.retrieval_temperature,
            scopes=recall_scopes,
        )
        if memories_text:
            dynamic_parts.append(f"## Relevant memories\n{memories_text}")

        if random_budget > 0:
            random_memories = memory.recall_with_budget(
                "",
                min_score=0.1,
                max_tokens=random_budget,
                retrieval_temperature=1.0,
                scopes=recall_scopes,
            )
            if random_memories:
                dynamic_parts.append(f"## Serendipitous memories (for creative inspiration)\n{random_memories}")

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

        # Find a safe split point: must land on a "user" message so we never
        # separate an assistant-with-tool_use block from its tool_result(s).
        split = len(messages) - keep
        while split > 0 and messages[split].role != "user":
            split -= 1

        if split <= 0:
            # Every message is part of a tool round — nothing safe to compact.
            return messages

        old_messages = messages[:split]
        recent_messages = messages[split:]

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

        # Build conversation text for summarization
        convo_text = "\n".join(
            f"{m.role}: {m.content if isinstance(m.content, str) else '[tool/content block]'}"
            for m in old_messages[:20]  # cap to avoid huge prompts
        )

        # Choose summary prompt by strategy
        if policy.compact_strategy == "abstractive":
            summary_prompt = (
                "You are compressing a conversation for long-term memory.\n"
                "Your task: Extract only the abstract PATTERNS, PRINCIPLES, and INSIGHTS.\n"
                "Rules:\n"
                "- DO NOT include specific facts, exact quotes, or proper nouns unless essential\n"
                "- DO NOT list what was discussed; describe what was LEARNED\n"
                "- Merge similar ideas into generalizations\n"
                "- Write in 3-5 bullet points maximum\n"
                "- Each bullet = one transferable principle\n\n"
                "Conversation to abstract:\n" + convo_text
            )
        else:
            # "lossless" and "summarize" both use the detail-preserving prompt
            summary_prompt = (
                "Summarize the following conversation excerpt in concise bullet points. "
                "Focus on key facts, decisions, and context needed for continuation.\n\n"
                + convo_text
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
            if policy.compact_strategy == "abstractive":
                memory.remember(
                    summary,
                    title=f"Abstract principles from session {session_id[:8]}",
                    tags=["_compact_abstractive", session_id],
                )
            compressed = [Message(role="user", content=f"[Compressed context]\n{summary}")]
            log.info("Context compacted: %d→%d messages", len(messages), len(compressed) + len(recent_messages))
            return compressed + recent_messages
        except Exception as e:
            log.error("Compact failed: %s — dropping oldest half instead", e)
            mid = len(messages) // 2
            while mid < len(messages) and messages[mid].role != "user":
                mid += 1
            return messages[mid:] if mid < len(messages) else messages

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
