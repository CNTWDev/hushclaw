"""AgentLoop: the core ReAct reasoning-and-acting loop."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator

from hushclaw.config.schema import Config
from hushclaw.context.engine import ContextEngine, DefaultContextEngine, needs_compaction
from hushclaw.context.policy import ContextPolicy
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.base import LLMProvider, Message, LLMResponse
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.registry import ToolRegistry
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.gateway import Gateway

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
        skill_registry=None,
        scheduler=None,
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
                workspace_dir=config.agent.workspace_dir,
            )

        # Session-level token counters
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        # Per-react-loop counters (reset at start of each public method)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        # Set by gateway during pipeline execution; cleared after each step.
        self.pipeline_run_id: str = ""

        self._context: list[Message] = []

        from hushclaw.browser import BrowserSession
        storage_state_path = None
        if config.browser.enabled and config.browser.persist_cookies:
            if config.memory.data_dir is not None:
                storage_state_path = config.memory.data_dir / "browser" / "cookies.json"
        self._browser_session = BrowserSession(
            headless=config.browser.headless,
            timeout_ms=config.browser.timeout * 1000,
            storage_state_path=storage_state_path,
        )
        # If remote_debugging_url is configured, schedule CDP auto-connect on first use.
        self._cdp_pending: bool = bool(
            config.browser.enabled and config.browser.remote_debugging_url
        )

        # Expose skill_registry directly so CLI / server code can access it without
        # going through the executor context dict.
        self._skill_registry = skill_registry

        self.executor = ToolExecutor(registry, timeout=config.tools.timeout)
        self.executor.set_context(
            _memory_store=memory,
            _config=config,
            _registry=registry,
            _session_id=self.session_id,
            _gateway=gateway,
            _loop=self,
            _skill_registry=skill_registry,
            _scheduler=scheduler,
            _browser=self._browser_session,
            _handover_registry=gateway.handover_registry if gateway is not None else {},
        )

    # ------------------------------------------------------------------
    # CDP auto-connect helper
    # ------------------------------------------------------------------

    async def _ensure_cdp(self) -> None:
        """Connect to the user's Chrome via CDP on first call (if configured)."""
        if not self._cdp_pending:
            return
        self._cdp_pending = False  # attempt once; don't retry on failure
        url = self.config.browser.remote_debugging_url
        try:
            tabs = await self._browser_session.connect_remote_chrome(url)
            log.info(
                "CDP auto-connected to %s — %d tab(s) open",
                url, len(tabs),
            )
        except Exception as exc:
            log.warning("CDP auto-connect to %s failed: %s", url, exc)

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
            memory_decay_rate=c.memory_decay_rate,
            retrieval_temperature=c.retrieval_temperature,
            serendipity_budget=c.serendipity_budget,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Process one user turn and return the assistant's final response."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        await self._ensure_cdp()

        policy = self._policy()
        stable, dynamic = await self.context_engine.assemble(
            user_input, policy, self.memory, self.config.agent,
            session_id=self.session_id,
            pipeline_run_id=self.pipeline_run_id,
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
            pipeline_run_id=self.pipeline_run_id,
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
        await self._ensure_cdp()

        policy = self._policy()
        stable, dynamic = await self.context_engine.assemble(
            user_input, policy, self.memory, self.config.agent,
            session_id=self.session_id,
            pipeline_run_id=self.pipeline_run_id,
        )
        system: str | tuple[str, str] = (stable, dynamic) if dynamic else stable

        self._sanitize_context()   # clean up dangling tool_use blocks from interrupted/restored sessions
        self._context.append(Message(role="user", content=user_input))
        tools = self.registry.to_api_schemas() if self.registry else None
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model

        log.info(
            "event_stream start: session=%s model=%s input=%r",
            self.session_id[:12], model, user_input[:80],
        )

        # Save user turn before tools execute so DB order is user → tool → assistant.
        # Token counts aren't known yet; they will be updated after the loop.
        _user_turn_id = self.memory.save_turn(self.session_id, "user", user_input)

        full_text: list[str] = []
        _call_cache: dict[str, str] = {}  # canonical_key → result_content (per-turn dedup)
        _agent_update_tools = {"create_agent", "update_agent", "spawn_agent"}
        _agent_update_tool_calls = 0

        round_num = 0
        _last_stop_reason = "end_turn"
        while True:
            # Notify frontend that a new reasoning round is starting (round > 0 = after tool use)
            if round_num > 0:
                yield {"type": "round_info", "round": round_num, "max_rounds": max_rounds}

            # Compact if needed
            if needs_compaction(self._context, policy):
                old_count = len(self._context)
                self._context = await self.context_engine.compact(
                    self._context, policy, self.provider, model, self.memory, self.session_id
                )
                new_count = len(self._context)
                log.info(
                    "compaction: session=%s archived=%d kept=%d",
                    self.session_id[:12], old_count - new_count, new_count,
                )
                yield {
                    "type": "compaction",
                    "archived": old_count - new_count,
                    "kept": new_count,
                }

            log.info(
                "provider.complete: session=%s round=%d model=%s context_msgs=%d",
                self.session_id[:12], round_num, model, len(self._context),
            )
            complete_kwargs = dict(
                messages=self._context,
                system=system,
                tools=tools,
                model=model,
            )
            # max_tokens=0 means "no app-side cap" (provider/model default applies).
            if self.config.agent.max_tokens > 0:
                complete_kwargs["max_tokens"] = self.config.agent.max_tokens
            response = await self.provider.complete(**complete_kwargs)
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens
            _last_stop_reason = response.stop_reason or "end_turn"

            if response.content:
                full_text.append(response.content)
                # Only stream text to the frontend on the final round.
                # Intermediate rounds that also produce tool_calls generate
                # transient "thinking aloud" text that cannot be reliably
                # tracked by the frontend (the bubble gets detached when the
                # tool_call event arrives), leading to stale or empty bubbles.
                # The text is still accumulated in full_text and persisted.
                if response.stop_reason != "tool_use" or not response.tool_calls:
                    yield {"type": "chunk", "text": response.content}

            # #region agent log
            import json as _json2, time as _time2
            _is_final_round = response.stop_reason != "tool_use" or not response.tool_calls
            _dbg_round = {"sessionId":"94ef74","location":"loop.py:event_stream:round","message":"round result","data":{"session_id":self.session_id[:12],"round":round_num,"stop_reason":response.stop_reason,"has_tool_calls":bool(response.tool_calls),"tool_call_count":len(response.tool_calls) if response.tool_calls else 0,"content_len":len(response.content) if response.content else 0,"content_preview":(response.content or "")[:120],"text_yielded_to_frontend":_is_final_round and bool(response.content),"is_final_round":_is_final_round},"timestamp":int(_time2.time()*1000),"hypothesisId":"H-B H-C"}
            try:
                import urllib.request as _ur2
                _ur2.urlopen(_ur2.Request("http://127.0.0.1:7866/ingest/27d763d0-b753-40be-a694-9f8daadda668",data=_json2.dumps(_dbg_round).encode(),headers={"Content-Type":"application/json","X-Debug-Session-Id":"94ef74"},method="POST"),timeout=1)
            except Exception:
                pass
            # #endregion

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

            if max_rounds > 0 and round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached in event_stream", max_rounds)
                _last_stop_reason = "max_tool_rounds"
                # #region agent log
                import json as _json3, time as _time3
                _dbg_maxr = {"sessionId":"94ef74","location":"loop.py:max_rounds_break","message":"MAX ROUNDS HIT — breaking with dangling tool_use in context","data":{"session_id":self.session_id[:12],"round_num":round_num,"max_rounds":max_rounds,"tool_calls":[tc.name for tc in response.tool_calls] if response.tool_calls else []},"timestamp":int(_time3.time()*1000),"hypothesisId":"H-D"}
                try:
                    import urllib.request as _ur3
                    _ur3.urlopen(_ur3.Request("http://127.0.0.1:7866/ingest/27d763d0-b753-40be-a694-9f8daadda668",data=_json3.dumps(_dbg_maxr).encode(),headers={"Content-Type":"application/json","X-Debug-Session-Id":"94ef74"},method="POST"),timeout=1)
                except Exception:
                    pass
                # #endregion
                break

            # Execute tool calls, yielding visibility events
            for tc in response.tool_calls:
                if tc.name in _agent_update_tools:
                    _agent_update_tool_calls += 1
                key = tc.name + ":" + json.dumps(tc.input, sort_keys=True)
                if key in _call_cache:
                    # Duplicate call: inject cached result into context (required by API)
                    # but suppress frontend events to avoid duplicate bubbles.
                    log.debug("Dedup tool call (cached): %s(%s)", tc.name, tc.input)
                    self._context.append(Message(
                        role="tool",
                        content=_call_cache[key],
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    ))
                    continue

                yield {"type": "tool_call", "tool": tc.name, "input": tc.input, "call_id": tc.id}
                log.info("tool call: session=%s tool=%s input=%r", self.session_id[:12], tc.name, tc.input)
                result = await self.executor.execute(tc.name, tc.input)
                log.info("tool result: session=%s tool=%s ok=%s result=%r",
                         self.session_id[:12], tc.name, not result.is_error,
                         (result.content or "")[:120])
                _call_cache[key] = result.content
                self.memory.save_turn(
                    self.session_id, "tool", result.content, tool_name=tc.name
                )
                yield {"type": "tool_result", "tool": tc.name, "result": result.content, "call_id": tc.id, "is_error": result.is_error}
                self._context.append(Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))
            round_num += 1

        # Back-fill input token count on the user turn now that we know it.
        self.memory.update_turn_tokens(_user_turn_id, input_tokens=self._total_input_tokens)

        # Persist assistant turn (user turn was saved before the loop)
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

        log.info(
            "event_stream done: session=%s in_tokens=%d out_tokens=%d agent_update_tools_called=%d",
            self.session_id[:12], self._total_input_tokens, self._total_output_tokens, _agent_update_tool_calls,
        )
        yield {
            "type": "done",
            "text": final_text,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "stop_reason": _last_stop_reason,
            "rounds_used": round_num,
        }

    def debug_state(self) -> dict:
        """Return a snapshot of the current session state for /debug display."""
        from hushclaw.util.tokens import estimate_messages_tokens
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
            # Skip tool-role turns: they require tool_call_id to be valid, but
            # that field is not persisted to the DB.  Including them without the
            # matching tool_use blocks causes Anthropic API 400 errors.
            if t["role"] == "tool":
                continue
            self._context.append(Message(role=t["role"], content=t["content"]))

        # If there's a summary, use it as compressed context
        summary = self.memory.load_session_summary(session_id)
        if summary:
            self._context = [Message(role="user", content=f"[Session summary]\n{summary}")]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sanitize_context(self) -> None:
        """Remove dangling tool_use blocks that have no matching tool_result.

        Called at the start of every event_stream() turn so that an interrupted
        or restored session never sends unpaired tool_use ids to the API.

        Strategy:
        - Build a set of tool_call_ids that ARE satisfied (have a role=tool entry).
        - Walk the context; for any assistant message whose tool_use block ids are
          not all satisfied, strip the unsatisfied blocks from the content list.
        - If stripping leaves the assistant message with only a text block, collapse
          it to a plain string.  If it leaves the message empty, remove it entirely.
        - Also remove any orphaned role=tool messages whose tool_call_id no longer
          has a matching tool_use block in the preceding assistant message.
        """
        if not self._context:
            return

        # Collect satisfied tool_call_ids
        satisfied: set[str] = {
            m.tool_call_id
            for m in self._context
            if m.role == "tool" and m.tool_call_id
        }

        cleaned: list = []
        removed = 0
        active_tool_use_ids: set[str] = set()  # tool_use ids still in cleaned context

        for msg in self._context:
            if msg.role == "assistant" and isinstance(msg.content, list):
                unsatisfied = {
                    blk["id"]
                    for blk in msg.content
                    if isinstance(blk, dict) and blk.get("type") == "tool_use"
                    and blk.get("id") not in satisfied
                }
                if unsatisfied:
                    removed += len(unsatisfied)
                    new_content = [
                        blk for blk in msg.content
                        if not (
                            isinstance(blk, dict)
                            and blk.get("type") == "tool_use"
                            and blk.get("id") in unsatisfied
                        )
                    ]
                    if not new_content:
                        continue  # entire message was tool_use — drop it
                    # Collapse single text block back to plain string
                    if (
                        len(new_content) == 1
                        and isinstance(new_content[0], dict)
                        and new_content[0].get("type") == "text"
                    ):
                        cleaned.append(Message(role="assistant", content=new_content[0]["text"]))
                    else:
                        cleaned.append(Message(role="assistant", content=new_content))
                    # Track which tool_use ids survived into the cleaned context
                    for blk in new_content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            active_tool_use_ids.add(blk["id"])
                    continue

                # All tool_use ids in this message are satisfied — keep as-is
                for blk in msg.content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        active_tool_use_ids.add(blk["id"])
                cleaned.append(msg)

            elif msg.role == "tool":
                # Drop orphaned tool results whose tool_use block was removed
                if msg.tool_call_id in active_tool_use_ids:
                    cleaned.append(msg)
                elif msg.tool_call_id in satisfied:
                    # Result belongs to a tool_use that survived — keep it
                    cleaned.append(msg)
                else:
                    removed += 1  # orphaned tool result — drop
            else:
                cleaned.append(msg)

        if removed:
            log.warning(
                "[loop] Stripped %d dangling tool_use/tool_result block(s) from context "
                "(task was interrupted or session was restored without tool results).",
                removed,
            )
            self._context = cleaned
        # #region agent log
        import json as _json, time as _time
        _dbg_sanitize = {"sessionId":"94ef74","location":"loop.py:_sanitize_context","message":"sanitize_context result","data":{"session_id":self.session_id[:12],"removed":removed,"satisfied_count":len(satisfied),"context_len":len(self._context)},"timestamp":int(_time.time()*1000),"hypothesisId":"H-A"}
        try:
            import urllib.request as _ur
            _ur.urlopen(_ur.Request("http://127.0.0.1:7866/ingest/27d763d0-b753-40be-a694-9f8daadda668",data=_json.dumps(_dbg_sanitize).encode(),headers={"Content-Type":"application/json","X-Debug-Session-Id":"94ef74"},method="POST"),timeout=1)
        except Exception:
            pass
        # #endregion

    async def _react_loop(
        self,
        system: "str | tuple[str, str]",
        tools: list[dict] | None,
        policy: ContextPolicy,
    ) -> LLMResponse:
        """Run the ReAct loop: call LLM, execute tools, repeat."""
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model

        round_num = 0
        while True:
            # Compact if needed
            if needs_compaction(self._context, policy):
                self._context = await self.context_engine.compact(
                    self._context, policy, self.provider, model, self.memory, self.session_id
                )

            complete_kwargs = dict(
                messages=self._context,
                system=system,
                tools=tools,
                model=model,
            )
            # max_tokens=0 means "no app-side cap" (provider/model default applies).
            if self.config.agent.max_tokens > 0:
                complete_kwargs["max_tokens"] = self.config.agent.max_tokens
            response = await self.provider.complete(**complete_kwargs)
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens

            log.info(
                "_react_loop round=%d stop_reason=%s tool_calls=%d",
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

            if max_rounds > 0 and round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached", max_rounds)
                return response

            # Execute all tool calls
            for tc in response.tool_calls:
                log.info("tool call (_react_loop): tool=%s input=%r", tc.name, tc.input)
                result = await self.executor.execute(tc.name, tc.input)
                log.info("tool result (_react_loop): tool=%s ok=%s result=%r",
                         tc.name, not result.is_error, (result.content or "")[:120])
                self.memory.save_turn(
                    self.session_id, "tool", result.content, tool_name=tc.name
                )
                self._context.append(Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))
            round_num += 1

        # Should not reach here
        return LLMResponse(content="", stop_reason="end_turn")
