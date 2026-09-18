"""Context compaction with explicit coverage and failure-safe retention."""
from __future__ import annotations

import json
from dataclasses import replace

from hushclaw.context.policy import ContextPolicy
from hushclaw.prompts import COMPACT_LOSSLESS_TEMPLATE, COMPACT_SUMMARY_PREFIX, COMPACT_SYSTEM, COMPACT_UPDATE_TEMPLATE
from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger
from hushclaw.util.tokens import estimate_messages_tokens

log = get_logger("context.compactor")
_MAX_CONVO_CHARS = 120_000


class CompactionService:
    """Summaries replace a known prefix, never an unrepresented tail."""

    @staticmethod
    def _is_user_turn(message: Message) -> bool:
        return message.role == "user" and not message.context_kind and not (
            isinstance(message.content, str) and message.content.startswith(
                (COMPACT_SUMMARY_PREFIX, "[Working state]\n", "[Session summary]\n")
            )
        )

    @staticmethod
    def _split_at_recent_user_turn(messages, keep_user_turns):
        keep = max(1, int(keep_user_turns or 1))
        indices = [i for i, m in enumerate(messages) if CompactionService._is_user_turn(m)]
        if len(indices) <= keep:
            return [], messages
        boundary = indices[-keep]
        return messages[:boundary], messages[boundary:]

    @staticmethod
    def _target_tokens(policy: ContextPolicy) -> int:
        return int(policy.history_budget * policy.compact_threshold) if policy.history_budget > 0 else 0

    @staticmethod
    def prune_tool_results(messages, policy):
        old, recent = CompactionService._split_at_recent_user_turn(messages, policy.compact_keep_turns)
        # Preserve call IDs and names: otherwise sanitization discards the pair.
        return [replace(m, content="<pruned>") if m.role == "tool" else m for m in old] + recent

    @staticmethod
    def _extract_prior_summary(messages):
        if messages and isinstance(messages[0].content, str):
            if messages[0].content.startswith(COMPACT_SUMMARY_PREFIX):
                return messages[0].content[len(COMPACT_SUMMARY_PREFIX):].strip()
        return None

    async def compact(self, messages, policy, provider, model, memory, session_id):
        if policy.compact_strategy == "prune_tool_results":
            return self.prune_tool_results(messages, policy)

        # Finalize the retained boundary BEFORE calling the model. If the summary
        # is larger than expected, leave it over the soft target; never discard
        # more turns that the summary has not seen.
        keep = max(1, int(policy.compact_keep_turns or 1))
        old, recent = self._split_at_recent_user_turn(messages, keep)
        target = self._target_tokens(policy)
        while keep > 1 and target > 0 and estimate_messages_tokens(recent) + 2048 >= target:
            keep -= 1
            old, recent = self._split_at_recent_user_turn(messages, keep)
        if not old:
            return messages

        try:
            checkpoint = None
            if recent[0].source_id:
                checkpoint = memory.prepare_context_checkpoint(session_id, recent[0].source_id)
            prior = self._extract_prior_summary(old)
            lines = []
            for index, message in enumerate(old):
                if index == 0 and prior:
                    continue
                if message.context_kind == "working_state":
                    continue
                content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
                lines.append(f"{message.role}: {content}")
            conversation = "\n\n".join(lines)
            # Every character of the selected prefix is processed. Oversized
            # inputs are merged incrementally instead of silently truncated.
            chunks = [conversation[i:i + _MAX_CONVO_CHARS] for i in range(0, len(conversation), _MAX_CONVO_CHARS)] or [""]
            summary = prior
            for chunk in chunks:
                prompt = (COMPACT_UPDATE_TEMPLATE.format(prior=summary, new_events=chunk) if summary
                          else COMPACT_LOSSLESS_TEMPLATE + "\n\n" + chunk)
                response = await provider.complete(
                    messages=[Message(role="user", content=prompt)], system=COMPACT_SYSTEM,
                    max_tokens=2048, model=model,
                )
                if not isinstance(response.content, str) or not response.content.strip():
                    raise ValueError("Empty context summary")
                if response.stop_reason not in {"end_turn", "stop_sequence"} or response.tool_calls:
                    raise ValueError("Incomplete context summary")
                summary = response.content.strip()

            if isinstance(checkpoint, dict):
                if not memory.save_context_checkpoint(checkpoint, summary):
                    log.warning("History changed during compaction; preserving original context")
                    return messages
            compressed = [Message(role="user", content=f"{COMPACT_SUMMARY_PREFIX}\n{summary}", context_kind="summary")]
            working_state = memory.load_session_working_state(session_id)
            if isinstance(working_state, str) and working_state:
                compressed.append(Message(role="user", content=f"[Working state]\n{working_state}", context_kind="working_state"))
        except Exception:
            log.error("Compaction failed — preserving original conversation", exc_info=True)
            return messages

        # Optional archival failure must not undo a valid checkpoint or remove
        # history. Raw turns/events remain regardless of archive strategy.
        if policy.compact_strategy == "lossless":
            try:
                memory.remember(
                    "\n\n".join(f"[{m.role}]: {m.content}" for m in old),
                    title=f"Archived context for session {session_id[:8]}",
                    tags=["_compact_archive", session_id], memory_kind="session_memory",
                )
            except Exception:
                log.warning("Optional context archive failed; durable history retained", exc_info=True)
        log.info("Context compacted: %d→%d messages; retained %d recent messages", len(messages), len(compressed) + len(recent), len(recent))
        return compressed + recent
