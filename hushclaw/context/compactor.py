"""Context compaction service."""
from __future__ import annotations

from hushclaw.context.policy import ContextPolicy
from hushclaw.prompts import (
    COMPACT_ABSTRACTIVE_TEMPLATE,
    COMPACT_LOSSLESS_TEMPLATE,
    COMPACT_SUMMARY_PREFIX,
    COMPACT_SYSTEM,
)
from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger
from hushclaw.util.tokens import estimate_messages_tokens

log = get_logger("context.compactor")


class CompactionService:
    """Lightweight service for shrinking message history under budget."""

    @staticmethod
    def _split_at_recent_user_turn(
        messages: list[Message],
        keep_user_turns: int,
    ) -> tuple[list[Message], list[Message]]:
        """Split history so the recent side starts at a user turn boundary."""
        keep = max(1, int(keep_user_turns or 1))
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= keep:
            return [], messages
        boundary = user_indices[-keep]
        return messages[:boundary], messages[boundary:]

    @staticmethod
    def _target_tokens(policy: ContextPolicy) -> int:
        if policy.history_budget <= 0:
            return 0
        return int(policy.history_budget * policy.compact_threshold)

    @staticmethod
    def prune_tool_results(
        messages: list[Message],
        policy: ContextPolicy,
    ) -> list[Message]:
        """Replace older tool outputs with ``<pruned>`` placeholders."""
        keep = policy.compact_keep_turns
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= keep:
            return messages

        boundary = user_indices[-keep]
        result: list[Message] = []
        pruned_count = 0
        for i, msg in enumerate(messages):
            if i >= boundary:
                result.append(msg)
            elif msg.role == "tool":
                result.append(Message(role="tool", content="<pruned>"))
                pruned_count += 1
            else:
                result.append(msg)

        log.debug(
            "prune_tool_results: pruned %d tool messages (kept last %d rounds)",
            pruned_count, keep,
        )
        return result

    async def compact(
        self,
        messages: list[Message],
        policy: ContextPolicy,
        provider,
        model: str,
        memory,
        session_id: str,
    ) -> list[Message]:
        if policy.compact_strategy == "prune_tool_results":
            return self.prune_tool_results(messages, policy)

        old_messages, initial_recent_messages = self._split_at_recent_user_turn(
            messages,
            policy.compact_keep_turns,
        )
        if not old_messages:
            return messages

        if policy.compact_strategy == "lossless":
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
                    memory_kind="session_memory",
                )

        # Feed all old messages to the summary LLM, but cap total chars so the
        # prompt stays within a reasonable size (~120k chars ≈ ~30k tokens).
        _MAX_CONVO_CHARS = 120_000
        convo_lines = []
        used = 0
        for m in old_messages:
            line = f"{m.role}: {m.content if isinstance(m.content, str) else '[tool/content block]'}"
            if used + len(line) > _MAX_CONVO_CHARS:
                convo_lines.append("[…earlier messages truncated for summary length…]")
                break
            convo_lines.append(line)
            used += len(line)
        convo_text = "\n".join(convo_lines)

        if policy.compact_strategy == "abstractive":
            summary_prompt = COMPACT_ABSTRACTIVE_TEMPLATE + "\n\nConversation to abstract:\n" + convo_text
        else:
            summary_prompt = COMPACT_LOSSLESS_TEMPLATE + "\n\n" + convo_text
        try:
            resp = await provider.complete(
                messages=[Message(role="user", content=summary_prompt)],
                system=COMPACT_SYSTEM,
                max_tokens=2048,
                model=model,
            )
            summary = resp.content
            memory.save_session_summary(session_id, summary)
            if policy.compact_strategy == "abstractive":
                memory.remember(
                    summary,
                    title=f"Abstract principles from session {session_id[:8]}",
                    tags=["_compact_abstractive", session_id],
                    memory_kind="session_memory",
                )
            compressed = [Message(role="user", content=f"{COMPACT_SUMMARY_PREFIX}\n{summary}")]
            working_state = memory.load_session_working_state(session_id)
            if working_state:
                compressed.append(Message(role="user", content=f"[Working state]\n{working_state}"))

            recent_messages = initial_recent_messages
            target_tokens = self._target_tokens(policy)
            if target_tokens > 0:
                keep = max(1, int(policy.compact_keep_turns or 1))
                while keep > 1 and estimate_messages_tokens(compressed + recent_messages) >= target_tokens:
                    keep -= 1
                    _old, recent_messages = self._split_at_recent_user_turn(messages, keep)

            log.info(
                "Context compacted: %d→%d messages",
                len(messages),
                len(compressed) + len(recent_messages),
            )
            return compressed + recent_messages
        except Exception:
            log.error("Compact failed — dropping oldest half instead", exc_info=True)
            mid = len(messages) // 2
            while mid < len(messages) and messages[mid].role != "user":
                mid += 1
            return messages[mid:] if mid < len(messages) else messages
