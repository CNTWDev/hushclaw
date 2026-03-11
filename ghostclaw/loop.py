"""AgentLoop: the core ReAct reasoning-and-acting loop."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator

from ghostclaw.config.schema import Config
from ghostclaw.context.engine import ContextEngine, DefaultContextEngine, needs_compaction
from ghostclaw.context.policy import ContextPolicy
from ghostclaw.memory.store import MemoryStore
from ghostclaw.providers.base import LLMProvider, Message, LLMResponse
from ghostclaw.tools.executor import ToolExecutor
from ghostclaw.tools.registry import ToolRegistry
from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger

if TYPE_CHECKING:
    from ghostclaw.gateway import Gateway

log = get_logger("loop")


class AgentLoop:
    """
    ReAct-style agent loop:
      assemble_context → check_compaction → provider.complete
        → (tool_use → execute → re-call) | end_turn
        → persist_turn → after_turn_hook → output
    """

    def __init__(
        self,
        config: Config,
        provider: LLMProvider,
        memory: MemoryStore,
        registry: ToolRegistry,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
        context_engine: ContextEngine | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.memory = memory
        self.registry = registry
        self.session_id = session_id or make_id("s-")
        self.gateway = gateway

        if context_engine is not None:
            self.context_engine: ContextEngine = context_engine
        else:
            self.context_engine = DefaultContextEngine(
                auto_extract=config.context.auto_extract,
            )

        # Session-level token counters
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        # Per-react-loop counters (reset at start of each public method)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        self._context: list[Message] = []

        self.executor = ToolExecutor(registry, timeout=config.tools.timeout)
        self.executor.set_context(
            _memory_store=memory,
            _config=config,
            _registry=registry,
            _session_id=self.session_id,
            _gateway=gateway,
            _loop=self,
        )

    # ------------------------------------------------------------------
    # Context policy (derived from config)
    # ------------------------------------------------------------------

    def _policy(self) -> ContextPolicy:
        c = self.config.context
        return ContextPolicy(
            stable_budget=c.stable_budget,
            dynamic_budget=c.dynamic_budget,
            history_budget=c.history_budget,
            compact_threshold=c.compact_threshold,
            compact_keep_turns=c.compact_keep_turns,
            compact_strategy=c.compact_strategy,
            memory_min_score=c.memory_min_score,
            memory_max_tokens=c.memory_max_tokens,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Process one user turn and return the assistant's final response."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        policy = self._policy()
        stable, dynamic = await self.context_engine.assemble(
            user_input, policy, self.memory, self.config.agent,
            session_id=self.session_id,
        )
        system: str | tuple[str, str] = (stable, dynamic) if dynamic else stable

        self._context.append(Message(role="user", content=user_input))
        tools = self.registry.to_api_schemas() if self.registry else None

        response = await self._react_loop(system, tools, policy)
        text = response.content

        # Persist turns with token counts
        self.memory.save_turn(
            self.session_id, "user", user_input,
            input_tokens=self._total_input_tokens,
        )
        if text:
            self.memory.save_turn(
                self.session_id, "assistant", text,
                output_tokens=self._total_output_tokens,
            )

        # Update session-level counters
        self._session_input_tokens += self._total_input_tokens
        self._session_output_tokens += self._total_output_tokens

        await self.context_engine.after_turn(
            self.session_id, user_input, text or "", self.memory
        )
        return text

    async def stream_run(self, user_input: str) -> AsyncIterator[str]:
        """Stream the assistant's response, yielding text chunks."""
        policy = self._policy()
        stable, dynamic = await self.context_engine.assemble(
            user_input, policy, self.memory, self.config.agent,
            session_id=self.session_id,
        )
        system: str | tuple[str, str] = (stable, dynamic) if dynamic else stable

        chunks = []
        async for chunk in self.provider.stream(
            self._context + [Message(role="user", content=user_input)],
            system=system,
            tools=self.registry.to_api_schemas() if self.registry else None,
        ):
            chunks.append(chunk)
            yield chunk

        full = "".join(chunks)
        self._context.append(Message(role="user", content=user_input))
        self._context.append(Message(role="assistant", content=full))
        self.memory.save_turn(self.session_id, "user", user_input)
        self.memory.save_turn(self.session_id, "assistant", full)

    async def event_stream(self, user_input: str) -> AsyncIterator[dict]:
        """
        Run the ReAct loop yielding structured events for real-time WebSocket streaming.

        Event types:
          {"type": "chunk",       "text": "..."}
          {"type": "tool_call",   "tool": "...", "input": {...}}
          {"type": "tool_result", "tool": "...", "result": "..."}
          {"type": "done",        "text": "...", "input_tokens": N, "output_tokens": M}
        """
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        policy = self._policy()
        stable, dynamic = await self.context_engine.assemble(
            user_input, policy, self.memory, self.config.agent,
            session_id=self.session_id,
        )
        system: str | tuple[str, str] = (stable, dynamic) if dynamic else stable

        self._context.append(Message(role="user", content=user_input))
        tools = self.registry.to_api_schemas() if self.registry else None
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model

        full_text: list[str] = []

        for round_num in range(max_rounds + 1):
            # Compact if needed
            if needs_compaction(self._context, policy):
                old_count = len(self._context)
                self._context = await self.context_engine.compact(
                    self._context, policy, self.provider, model, self.memory, self.session_id
                )
                new_count = len(self._context)
                yield {
                    "type": "compaction",
                    "archived": old_count - new_count,
                    "kept": new_count,
                }

            response = await self.provider.complete(
                messages=self._context,
                system=system,
                tools=tools,
                max_tokens=self.config.agent.max_tokens,
                model=model,
            )
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens

            if response.content:
                full_text.append(response.content)
                yield {"type": "chunk", "text": response.content}

            # Append assistant message to context
            if response.tool_calls:
                content_blocks = []
                if response.content:
                    content_blocks.append({"type": "text", "text": response.content})
                for tc in response.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                self._context.append(Message(role="assistant", content=content_blocks))
            else:
                self._context.append(Message(role="assistant", content=response.content))

            if response.stop_reason != "tool_use" or not response.tool_calls:
                break

            if round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached in event_stream", max_rounds)
                break

            # Execute tool calls, yielding visibility events
            for tc in response.tool_calls:
                yield {"type": "tool_call", "tool": tc.name, "input": tc.input}
                log.debug("Executing tool: %s(%s)", tc.name, tc.input)
                result = await self.executor.execute(tc.name, tc.input)
                self.memory.save_turn(
                    self.session_id, "tool", result.content, tool_name=tc.name
                )
                yield {"type": "tool_result", "tool": tc.name, "result": result.content}
                self._context.append(Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))

        # Persist turns
        self.memory.save_turn(
            self.session_id, "user", user_input,
            input_tokens=self._total_input_tokens,
        )
        final_text = "".join(full_text)
        if final_text:
            self.memory.save_turn(
                self.session_id, "assistant", final_text,
                output_tokens=self._total_output_tokens,
            )

        self._session_input_tokens += self._total_input_tokens
        self._session_output_tokens += self._total_output_tokens

        await self.context_engine.after_turn(
            self.session_id, user_input, final_text, self.memory
        )

        yield {
            "type": "done",
            "text": final_text,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
        }

    def debug_state(self) -> dict:
        """Return a snapshot of the current session state for /debug display."""
        from ghostclaw.util.tokens import estimate_messages_tokens
        history_tokens = estimate_messages_tokens(self._context)
        return {
            "session_id": self.session_id,
            "history_turns": len(self._context),
            "history_tokens": history_tokens,
            "history_budget": self.config.context.history_budget,
            "compact_threshold": self.config.context.compact_threshold,
            "session_input_tokens": self._session_input_tokens,
            "session_output_tokens": self._session_output_tokens,
            "last_turn_input_tokens": self._total_input_tokens,
            "last_turn_output_tokens": self._total_output_tokens,
        }

    def restore_session(self, session_id: str) -> None:
        """Restore turns from a previous session into the active context."""
        self.session_id = session_id
        turns = self.memory.load_session_turns(session_id)
        self._context = []
        for t in turns:
            self._context.append(Message(role=t["role"], content=t["content"]))

        # If there's a summary, use it as compressed context
        summary = self.memory.load_session_summary(session_id)
        if summary:
            self._context = [Message(role="user", content=f"[Session summary]\n{summary}")]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _react_loop(
        self,
        system: "str | tuple[str, str]",
        tools: list[dict] | None,
        policy: ContextPolicy,
    ) -> LLMResponse:
        """Run the ReAct loop: call LLM, execute tools, repeat."""
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model

        for round_num in range(max_rounds + 1):
            # Compact if needed
            if needs_compaction(self._context, policy):
                self._context = await self.context_engine.compact(
                    self._context, policy, self.provider, model, self.memory, self.session_id
                )

            response = await self.provider.complete(
                messages=self._context,
                system=system,
                tools=tools,
                max_tokens=self.config.agent.max_tokens,
                model=model,
            )
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens

            log.debug(
                "Round %d: stop_reason=%s tools=%d",
                round_num, response.stop_reason, len(response.tool_calls),
            )

            # Append assistant message (may include tool_use blocks)
            if response.tool_calls:
                content_blocks = []
                if response.content:
                    content_blocks.append({"type": "text", "text": response.content})
                for tc in response.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                self._context.append(Message(role="assistant", content=content_blocks))
            else:
                self._context.append(Message(role="assistant", content=response.content))

            if response.stop_reason != "tool_use" or not response.tool_calls:
                return response

            if round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached", max_rounds)
                return response

            # Execute all tool calls
            for tc in response.tool_calls:
                log.debug("Executing tool: %s(%s)", tc.name, tc.input)
                result = await self.executor.execute(tc.name, tc.input)
                self.memory.save_turn(
                    self.session_id, "tool", result.content, tool_name=tc.name
                )
                self._context.append(Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))

        # Should not reach here
        return LLMResponse(content="", stop_reason="end_turn")
