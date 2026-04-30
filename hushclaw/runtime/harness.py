"""HarnessFactory: cold-start AgentLoop reconstruction from event log.

Phase 6 of the architecture upgrade. An AgentLoop is logically stateless:
all durable state lives in MemoryStore (turns, events, threads, runs).
HarnessFactory.rebuild_from_thread() proves this contract by creating a
fully-functional loop from nothing but a thread_id.

Warm-cache fields (browser sandbox, token counters) are intentionally reset
to fresh values; they do NOT need to be persisted because:
  - _sandbox: browser sessions are short-lived; a new session starts cleanly
  - _session_input/output_tokens: recovered from SUM of turns table on demand
  - _total_input/output_tokens: per-call ephemeral counters, reset each turn
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.agent import Agent
    from hushclaw.loop import AgentLoop

log = get_logger("harness")


class HarnessFactory:
    """Factory for creating cold-start AgentLoop instances from the event log."""

    @staticmethod
    def rebuild_from_thread(thread_id: str, agent: "Agent") -> "AgentLoop":
        """Reconstruct an AgentLoop from its thread_id + durable event log.

        Steps:
          1. Look up thread → session_id, agent_name
          2. Create a fresh AgentLoop via agent.new_loop(session_id)
             (new_loop already calls restore_session which loads turns from DB)
          3. Recover session-level token counters from the turns table
          4. Return the rebuilt loop — ready to resume execution

        The returned loop is indistinguishable from one that ran continuously.
        """
        memory = agent.memory
        row = memory.conn.execute(
            "SELECT session_id, agent_name FROM threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Thread not found: {thread_id}")

        session_id = row["session_id"]
        log.info(
            "cold-start rebuild: thread=%s session=%s",
            thread_id[:12],
            session_id[:12],
        )

        loop = agent.new_loop(session_id, thread_id=thread_id)
        replayed = HarnessFactory._restore_context_from_events(loop, memory, thread_id)
        if replayed:
            log.info(
                "rebuild_from_thread: restored context from events thread=%s messages=%d",
                thread_id[:12],
                len(loop._context),
            )

        loop._session_input_tokens, loop._session_output_tokens = HarnessFactory._recover_session_token_totals(
            memory,
            session_id,
        )

        log.info(
            "rebuilt harness: session=%s turns=%d tokens_in=%d tokens_out=%d",
            session_id[:12],
            len(loop._context),
            loop._session_input_tokens,
            loop._session_output_tokens,
        )
        return loop

    @staticmethod
    def rebuild_from_events(thread_id: str, agent: "Agent") -> "AgentLoop":
        """Reconstruct an AgentLoop using thread-scoped session events."""
        memory = agent.memory
        row = memory.conn.execute(
            "SELECT session_id FROM threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Thread not found: {thread_id}")

        session_id = row["session_id"]
        loop = agent.new_loop(session_id, thread_id=thread_id)
        if not HarnessFactory._restore_context_from_events(loop, memory, thread_id):
            raise ValueError(f"No replayable events found for thread: {thread_id}")

        loop._session_input_tokens, loop._session_output_tokens = HarnessFactory._recover_session_token_totals(
            memory,
            session_id,
        )
        return loop

    @staticmethod
    def _restore_context_from_events(loop: "AgentLoop", memory, thread_id: str) -> bool:
        rebuilt = memory.session_log.replay_context(thread_id=thread_id)
        if not rebuilt:
            return False

        summary = memory.load_session_summary(loop.session_id)
        if summary:
            from hushclaw.providers.base import Message
            loop._context = [Message(role="user", content=f"[Session summary]\n{summary}")]
        else:
            loop._context = rebuilt
        return True

    @staticmethod
    def _recover_session_token_totals(memory, session_id: str) -> tuple[int, int]:
        input_tokens, output_tokens = memory.session_log.replay_token_totals(session_id=session_id)
        if input_tokens or output_tokens:
            return input_tokens, output_tokens

        token_row = memory.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS inp, "
            "       COALESCE(SUM(output_tokens),0) AS out "
            "FROM turns WHERE session=?",
            (session_id,),
        ).fetchone()
        if token_row:
            return int(token_row["inp"]), int(token_row["out"])
        return 0, 0
