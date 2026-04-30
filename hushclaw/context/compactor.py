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

log = get_logger("context.compactor")


class CompactionService:
    """Lightweight service for shrinking message history under budget."""

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

        keep = policy.compact_keep_turns
        if len(messages) <= keep:
            return messages

        split = len(messages) - keep
        while split > 0 and messages[split].role != "user":
            split -= 1

        if split <= 0:
            return messages

        old_messages = messages[:split]
        recent_messages = messages[split:]

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

        convo_text = "\n".join(
            f"{m.role}: {m.content if isinstance(m.content, str) else '[tool/content block]'}"
            for m in old_messages[:20]
        )

        if policy.compact_strategy == "abstractive":
            summary_prompt = COMPACT_ABSTRACTIVE_TEMPLATE + "\n\nConversation to abstract:\n" + convo_text
        else:
            summary_prompt = COMPACT_LOSSLESS_TEMPLATE + "\n\n" + convo_text
        try:
            resp = await provider.complete(
                messages=[Message(role="user", content=summary_prompt)],
                system=COMPACT_SYSTEM,
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
                    memory_kind="session_memory",
                )
            compressed = [Message(role="user", content=f"{COMPACT_SUMMARY_PREFIX}\n{summary}")]
            working_state = memory.load_session_working_state(session_id)
            if working_state:
                compressed.append(Message(role="user", content=f"[Working state]\n{working_state}"))
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
