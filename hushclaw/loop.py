"""AgentLoop: the core ReAct reasoning-and-acting loop."""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, AsyncIterator

from hushclaw.config.schema import Config
from hushclaw.context.engine import ContextEngine, DefaultContextEngine, needs_compaction
from hushclaw.context.policy import ContextPolicy
from hushclaw.core.errors import classify_error, backoff
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.base import LLMProvider, Message, LLMResponse
from hushclaw.runtime.hooks import HookBus
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
        hook_bus: HookBus | None = None,
        skill_registry=None,
        skill_manager=None,
        scheduler=None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.memory = memory
        self.registry = registry
        self.session_id = session_id or make_id("s-")
        self.gateway = gateway
        self.hook_bus = hook_bus

        if context_engine is not None:
            self.context_engine: ContextEngine = context_engine
        else:
            self.context_engine = DefaultContextEngine(
                auto_extract=config.context.auto_extract,
                workspace_dir=config.agent.workspace_dir,
                calendar_timezone=getattr(config.calendar, "timezone", ""),
            )

        # Session-level token counters
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        # Per-react-loop counters (reset at start of each public method)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        # Set by gateway during pipeline execution; cleared after each step.
        self.pipeline_run_id: str = ""
        # Set by gateway: call depth in delegation chains (0 = top-level)
        self._delegation_depth: int = 0
        # Set by gateway: source that triggered this loop ("cli", "scheduler", "connector:telegram", …)
        self._source_channel: str = "cli"

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
        self._skill_manager  = skill_manager

        # Trajectory collection (optional — disabled when trajectory_dir is None)
        self._trajectory_writer = None
        if config.agent.trajectory_dir:
            from hushclaw.core.trajectory import TrajectoryWriter
            self._trajectory_writer = TrajectoryWriter(
                config.agent.trajectory_dir, self.session_id
            )

        self.executor = ToolExecutor(registry, timeout=config.tools.timeout)
        self.executor.set_context(
            _memory_store=memory,
            _config=config,
            _registry=registry,
            _session_id=self.session_id,
            _gateway=gateway,
            _loop=self,
            _skill_registry=skill_registry,
            _skill_manager=skill_manager,
            _scheduler=scheduler,
            _browser=self._browser_session,
            _handover_registry=gateway.handover_registry if gateway is not None else {},
            _output_dir=config.server.upload_dir,
            _delegation_depth=self._delegation_depth,
            _source_channel=self._source_channel,
        )

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Release async resources held by this loop (browser session, etc.)."""
        if self._browser_session is not None:
            try:
                await self._browser_session.close()
            except Exception as e:
                log.warning("browser session close error: %s", e)
            self._browser_session = None

    async def __aenter__(self) -> "AgentLoop":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()

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
    # Context helpers
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
            max_age_days=c.max_age_days,
        )

    async def _build_context(
        self,
        user_input: str,
        workspace_dir=None,
    ) -> tuple[str, str]:
        """Assemble (stable_prefix, dynamic_suffix) for one turn.

        Single entry point: all three public methods (run, stream_run, event_stream)
        call this instead of calling context_engine.assemble() directly. This ensures
        memory recall is performed exactly once per turn regardless of entry point.
        """
        return await self.context_engine.assemble(
            user_input,
            self._policy(),
            self.memory,
            self.config.agent,
            session_id=self.session_id,
            pipeline_run_id=self.pipeline_run_id,
            workspace_dir_override=workspace_dir,
        )

    def _hook_payload(self, **payload) -> dict:
        """Return a standard payload envelope for lifecycle hooks."""
        base = {
            "session_id": self.session_id,
            "pipeline_run_id": self.pipeline_run_id,
            "model": self.config.agent.model,
            "loop": self,
        }
        base.update(payload)
        return base

    async def _emit_hook(self, event_name: str, **payload) -> None:
        """Emit a lifecycle hook event if a hook bus is attached."""
        hook_bus = getattr(self, "hook_bus", None)
        if hook_bus is None:
            return
        await hook_bus.emit(event_name, **self._hook_payload(**payload))

    async def _compact_context(
        self,
        policy: ContextPolicy,
        model: str,
        *,
        reason: str,
    ) -> int:
        """Compact in-memory context and emit lifecycle hooks around it."""
        old_count = len(self._context)
        await self._emit_hook(
            "pre_compact",
            reason=reason,
            old_count=old_count,
            messages=self._context,
            policy=policy,
        )
        self._context = await self.context_engine.compact(
            self._context, policy, self.provider, model, self.memory, self.session_id
        )
        archived = old_count - len(self._context)
        await self._emit_hook(
            "post_compact",
            reason=reason,
            old_count=old_count,
            kept=len(self._context),
            archived=archived,
            messages=self._context,
            policy=policy,
        )
        return archived

    @staticmethod
    def _compose_system_prompt(stable: str, dynamic: str) -> "str | tuple[str, str]":
        """Keep provider-facing system prompt composition in one place."""
        return (stable, dynamic) if dynamic else stable

    def _tool_schemas(self) -> list[dict] | None:
        """Return tool schemas for the current registry, if any."""
        return self.registry.to_api_schemas() if self.registry else None

    @staticmethod
    def _memory_only_tool_names(tool_names: list[str]) -> bool:
        """Return True when all tool calls were memory-save side effects."""
        if not tool_names:
            return False
        return set(tool_names).issubset({"remember", "remember_skill"})

    async def _force_user_facing_answer(
        self,
        system: "str | tuple[str, str]",
        user_input: str,
    ) -> LLMResponse:
        """Recover from a turn that only performed memory-saving side effects."""
        reminder = (
            "You have already completed any memory-saving step for this turn. "
            "Now answer the user's latest request directly. "
            "Do not call tools. "
            "Do not mention saving to memory unless the user explicitly asked."
        )
        self._context.append(Message(role="user", content=reminder))
        try:
            return await self._call_provider(system, None, self.config.agent.model)
        finally:
            self._context.pop()

    async def _prepare_turn(
        self,
        user_input: str,
        *,
        entrypoint: str,
        workspace_dir=None,
        workspace_tag: str = "",
        ensure_cdp: bool = False,
    ) -> tuple[ContextPolicy, "str | tuple[str, str]", list[dict] | None]:
        """Shared turn prologue for all public loop entrypoints."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        payload = {"user_input": user_input, "entrypoint": entrypoint}
        if workspace_tag:
            payload["workspace"] = workspace_tag
        await self._emit_hook("pre_session_init", **payload)
        if ensure_cdp:
            await self._ensure_cdp()
        policy = self._policy()
        stable, dynamic = await self._build_context(user_input, workspace_dir=workspace_dir)
        return policy, self._compose_system_prompt(stable, dynamic), self._tool_schemas()

    async def _finalize_turn(
        self,
        user_input: str,
        assistant_response: str,
        *,
        entrypoint: str,
        workspace_tag: str = "",
    ) -> None:
        """Shared turn epilogue after persistence is complete."""
        self._session_input_tokens += self._total_input_tokens
        self._session_output_tokens += self._total_output_tokens
        await self.context_engine.after_turn(
            self.session_id, user_input, assistant_response or "", self.memory
        )
        payload = {
            "user_input": user_input,
            "assistant_response": assistant_response or "",
            "entrypoint": entrypoint,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
        }
        if workspace_tag:
            payload["workspace"] = workspace_tag
        await self._emit_hook("post_turn_persist", **payload)

    async def _background_finalize(
        self,
        *,
        user_input: str,
        final_text: str,
        workspace_tag: str,
        model: str,
        round_num: int,
        last_stop_reason: str,
        traj_tool_calls: list[dict],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Run after-turn work that does not block the done event.

        Covers context_engine.after_turn() and trajectory recording.
        post_turn_persist is emitted synchronously before done (see event_stream)
        so that LearningController's _pending data capture is race-free.
        """
        try:
            await self.context_engine.after_turn(
                self.session_id, user_input, final_text or "", self.memory
            )
        except Exception as e:
            log.warning("background after_turn failed: %s", e)
        if self._trajectory_writer is not None:
            try:
                self._trajectory_writer.record(
                    session_id=self.session_id,
                    user_input=user_input,
                    assistant_text=final_text,
                    tool_calls=traj_tool_calls,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    rounds=round_num,
                    stop_reason=last_stop_reason,
                )
            except Exception as e:
                log.warning("trajectory recording failed (turn not interrupted): %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Process one user turn and return the assistant's final response."""
        policy, system, tools = await self._prepare_turn(
            user_input,
            entrypoint="run",
            ensure_cdp=True,
        )

        self._context.append(Message(role="user", content=user_input))
        response = await self._react_loop(system, tools, policy, user_input=user_input)
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
        await self._finalize_turn(user_input, text, entrypoint="run")
        return text

    async def stream_run(self, user_input: str) -> AsyncIterator[str]:
        """Stream the assistant's response, yielding text chunks."""
        _policy, system, tools = await self._prepare_turn(
            user_input,
            entrypoint="stream_run",
        )

        chunks = []
        await self._emit_hook(
            "pre_llm_call",
            entrypoint="stream_run",
            system=system,
            tools=tools,
            messages=self._context + [Message(role="user", content=user_input)],
            active_model=self.config.agent.model,
        )
        async for chunk in self.provider.stream(
            self._context + [Message(role="user", content=user_input)],
            system=system,
            tools=tools,
        ):
            chunks.append(chunk)
            yield chunk

        full = "".join(chunks)
        await self._emit_hook(
            "post_llm_call",
            entrypoint="stream_run",
            active_model=self.config.agent.model,
            response_text=full,
            stop_reason="end_turn",
            input_tokens=0,
            output_tokens=0,
        )
        self._context.append(Message(role="user", content=user_input))
        self._context.append(Message(role="assistant", content=full))
        self.memory.save_turn(self.session_id, "user", user_input)
        self.memory.save_turn(self.session_id, "assistant", full)
        await self._finalize_turn(user_input, full, entrypoint="stream_run")

    async def event_stream(self, user_input: str, images: list[str] | None = None, workspace_dir=None) -> AsyncIterator[dict]:
        """
        Run the ReAct loop yielding structured events for real-time WebSocket streaming.

        Event types:
          {"type": "chunk",       "text": "..."}
          {"type": "tool_call",   "tool": "...", "input": {...}}
          {"type": "tool_result", "tool": "...", "result": "..."}
          {"type": "done",        "text": "...", "input_tokens": N, "output_tokens": M}
        """
        _t0 = time.monotonic()
        _workspace_tag: str = workspace_dir.name if workspace_dir else ""

        policy, system, tools = await self._prepare_turn(
            user_input,
            entrypoint="event_stream",
            workspace_dir=workspace_dir,
            workspace_tag=_workspace_tag,
            ensure_cdp=True,
        )

        log.info(
            "event_stream start: session=%s model=%s input=%r assemble=%.0fms",
            self.session_id[:12], self.config.agent.model, user_input[:80],
            (time.monotonic() - _t0) * 1000,
        )

        self._sanitize_context()
        self._context.append(Message(role="user", content=user_input, images=list(images or [])))
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model
        cheap_model = self.config.agent.cheap_model or ""

        _user_turn_id = self.memory.save_turn(self.session_id, "user", user_input, workspace=_workspace_tag)

        full_text: list[str] = []
        final_text = ""
        _call_cache: dict[str, str] = {}
        _agent_update_tool_calls = 0
        _agent_update_tools = {"create_agent", "update_agent", "spawn_agent"}
        _last_stop_reason = "end_turn"
        round_num = 0
        _tool_names_this_turn: list[str] = []
        _registry = self.registry  # local alias for use in nested helpers

        while True:
            if round_num > 0:
                yield {"type": "round_info", "round": round_num, "max_rounds": max_rounds}

            # Compact history if over budget — use cheap_model for summarization
            if needs_compaction(self._context, policy):
                compact_model = cheap_model or model
                archived = await self._compact_context(policy, compact_model, reason="event_stream_budget")
                yield {"type": "compaction", "archived": archived, "kept": len(self._context)}

            # Smart model routing: use cheap_model on round 0 when configured.
            # Skip cheap_model for complex tasks detected via length or keyword signals.
            # If the cheap model asks for tool use, the next round uses the full model.
            _COMPLEX_SIGNALS = (
                "write code", "implement", "refactor", "fix bug", "debug",
                "写代码", "实现", "重构", "修复", "设计架构",
                "analyze", "分析", "compare", "对比",
            )
            _is_complex = (
                len(user_input) > 500
                or any(s in user_input.lower() for s in _COMPLEX_SIGNALS)
            )
            active_model = (
                cheap_model if (cheap_model and round_num == 0 and not _is_complex) else model
            )
            # ── Streaming-first provider call ───────────────────────────────
            _stream_fn = getattr(self.provider, "stream_complete", None)
            response: LLMResponse | None = None
            _ft_start = len(full_text)  # track position for rollback on fallback

            if _stream_fn is not None:
                try:
                    _stream_kwargs: dict = dict(
                        messages=self._context,
                        system=system,
                        tools=tools,
                        model=active_model,
                    )
                    if self.config.agent.max_tokens > 0:
                        _stream_kwargs["max_tokens"] = self.config.agent.max_tokens
                    await self._emit_hook(
                        "pre_llm_call",
                        entrypoint="event_stream",
                        system=system,
                        tools=tools,
                        messages=self._context,
                        active_model=active_model,
                        attempt=1,
                    )
                    async for _item in _stream_fn(**_stream_kwargs):
                        if isinstance(_item, LLMResponse):
                            response = _item
                        elif isinstance(_item, str) and _item:
                            full_text.append(_item)
                            yield {"type": "chunk", "text": _item}
                    if response is not None:
                        self._total_input_tokens += response.input_tokens
                        self._total_output_tokens += response.output_tokens
                        await self._emit_hook(
                            "post_llm_call",
                            entrypoint="event_stream",
                            active_model=active_model,
                            response=response,
                            response_text=response.content,
                            stop_reason=response.stop_reason,
                            input_tokens=response.input_tokens,
                            output_tokens=response.output_tokens,
                            attempt=1,
                        )
                except Exception as _stream_exc:
                    log.warning("stream_complete failed, falling back to complete(): %s", _stream_exc)
                    response = None
                    del full_text[_ft_start:]  # remove any partial chunks from this round

            if response is None:
                response = await self._call_provider(
                    system, tools, active_model,
                    _skip_pre_hook=(_stream_fn is not None),
                )
                if response.content:
                    full_text.append(response.content)
                    if response.stop_reason != "tool_use" or not response.tool_calls:
                        yield {"type": "chunk", "text": response.content}
            # ────────────────────────────────────────────────────────────────

            _last_stop_reason = response.stop_reason or "end_turn"
            if active_model != model:
                log.debug("smart-routing: used cheap_model=%s stop=%s", active_model, response.stop_reason)

            self._append_assistant_message(response)

            if response.stop_reason != "tool_use" or not response.tool_calls:
                break
            if max_rounds > 0 and round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached in event_stream", max_rounds)
                _last_stop_reason = "max_tool_rounds"
                break

            # Execute tool calls — parallel-safe tools run concurrently, serial tools run sequentially.
            dedup_tcs: list[tuple] = []
            parallel_tcs: list[tuple] = []
            serial_tcs: list[tuple] = []
            for tc in response.tool_calls:
                _tool_names_this_turn.append(tc.name)
                if tc.name in _agent_update_tools:
                    _agent_update_tool_calls += 1
                key = tc.name + ":" + json.dumps(tc.input, sort_keys=True)
                if key in _call_cache:
                    dedup_tcs.append((tc, key))
                elif (td := _registry.get(tc.name)) and td.parallel_safe:
                    parallel_tcs.append((tc, key))
                else:
                    serial_tcs.append((tc, key))

            # Dedup: replay cached results into context without re-execution
            for tc, key in dedup_tcs:
                log.debug("Dedup tool call (cached): %s(%s)", tc.name, tc.input)
                self._context.append(Message(
                    role="tool", content=_call_cache[key],
                    tool_call_id=tc.id, tool_name=tc.name,
                ))

            # Parallel-safe tools: emit all tool_call events, gather concurrently, emit results
            if parallel_tcs:
                for tc, key in parallel_tcs:
                    yield {"type": "tool_call", "tool": tc.name, "input": tc.input, "call_id": tc.id}
                    await self._emit_hook(
                        "pre_tool_call",
                        entrypoint="event_stream",
                        tool_name=tc.name,
                        tool_input=tc.input,
                        call_id=tc.id,
                        workspace=_workspace_tag,
                    )

                _t_parallel = time.monotonic()

                async def _run_one(tc_key):
                    _tc, _key = tc_key
                    _t = time.monotonic()
                    _res = await self.executor.execute(_tc.name, _tc.input)
                    return _tc, _key, _res, time.monotonic() - _t

                parallel_results = await asyncio.gather(*[_run_one(pair) for pair in parallel_tcs])
                log.debug(
                    "parallel tools done: %.0fms for %d tools",
                    (time.monotonic() - _t_parallel) * 1000, len(parallel_tcs),
                )

                for tc, key, result, elapsed in parallel_results:
                    log.info("tool: session=%s %s ok=%s %.0fms result=%r",
                             self.session_id[:12], tc.name, not result.is_error,
                             elapsed * 1000, (result.content or "")[:120])
                    await self._emit_hook(
                        "post_tool_call",
                        entrypoint="event_stream",
                        tool_name=tc.name,
                        tool_input=tc.input,
                        tool_result=result.content,
                        is_error=result.is_error,
                        call_id=tc.id,
                        workspace=_workspace_tag,
                    )
                    _call_cache[key] = result.content
                    self.memory.save_turn(self.session_id, "tool", result.content,
                                          tool_name=tc.name, workspace=_workspace_tag)
                    yield {"type": "tool_result", "tool": tc.name, "result": result.content,
                           "call_id": tc.id, "is_error": result.is_error}
                    self._context.append(Message(
                        role="tool", content=result.content,
                        tool_call_id=tc.id, tool_name=tc.name,
                    ))

            # Serial tools: execute sequentially (state-mutating or otherwise unsafe to parallelize)
            for tc, key in serial_tcs:
                yield {"type": "tool_call", "tool": tc.name, "input": tc.input, "call_id": tc.id}
                _t_tool = time.monotonic()
                await self._emit_hook(
                    "pre_tool_call",
                    entrypoint="event_stream",
                    tool_name=tc.name,
                    tool_input=tc.input,
                    call_id=tc.id,
                    workspace=_workspace_tag,
                )
                result = await self.executor.execute(tc.name, tc.input)
                log.info("tool: session=%s %s ok=%s %.0fms result=%r",
                         self.session_id[:12], tc.name, not result.is_error,
                         (time.monotonic() - _t_tool) * 1000, (result.content or "")[:120])
                await self._emit_hook(
                    "post_tool_call",
                    entrypoint="event_stream",
                    tool_name=tc.name,
                    tool_input=tc.input,
                    tool_result=result.content,
                    is_error=result.is_error,
                    call_id=tc.id,
                    workspace=_workspace_tag,
                )
                _call_cache[key] = result.content
                self.memory.save_turn(self.session_id, "tool", result.content,
                                      tool_name=tc.name, workspace=_workspace_tag)
                yield {"type": "tool_result", "tool": tc.name, "result": result.content,
                       "call_id": tc.id, "is_error": result.is_error}
                self._context.append(Message(
                    role="tool", content=result.content,
                    tool_call_id=tc.id, tool_name=tc.name,
                ))
            round_num += 1

        final_text = "".join(full_text)
        if not final_text and self._memory_only_tool_names(_tool_names_this_turn):
            recovery = await self._force_user_facing_answer(system, user_input)
            if recovery.content:
                full_text.append(recovery.content)
                final_text = "".join(full_text)
                yield {"type": "chunk", "text": recovery.content}
                self._append_assistant_message(recovery)
                _last_stop_reason = recovery.stop_reason or "end_turn"
            else:
                final_text = "".join(full_text)

        # Persist turns (essential — must complete before done event)
        self.memory.update_turn_tokens(_user_turn_id, input_tokens=self._total_input_tokens)
        if final_text:
            self.memory.save_turn(self.session_id, "assistant", final_text,
                                  output_tokens=self._total_output_tokens, workspace=_workspace_tag)

        # Capture token counts as locals — a new turn could reset the instance counters
        _input_tokens = self._total_input_tokens
        _output_tokens = self._total_output_tokens
        self._session_input_tokens += _input_tokens
        self._session_output_tokens += _output_tokens

        # Capture trajectory data while _call_cache and response are still in scope
        _traj_tool_calls: list[dict] = []
        if self._trajectory_writer is not None:
            _last_tool_calls = response.tool_calls or []
            _traj_tool_calls = [
                {"name": tc.name, "input": tc.input,
                 "result": _call_cache.get(tc.name + ":" + json.dumps(tc.input, sort_keys=True), ""),
                 "is_error": False}
                for tc in _last_tool_calls
            ]

        # Emit post_turn_persist synchronously before done.
        # This keeps _pending data capture in LearningController race-free:
        # on_post_turn_persist() pops _pending[session_id] here, before any
        # next-turn pre_session_init can reset it.  SQLite writes are scheduled
        # inside the controller via asyncio.create_task (see controller.py).
        _persist_payload: dict = {
            "user_input": user_input,
            "assistant_response": final_text or "",
            "entrypoint": "event_stream",
            "input_tokens": _input_tokens,
            "output_tokens": _output_tokens,
        }
        if _workspace_tag:
            _persist_payload["workspace"] = _workspace_tag
        await self._emit_hook("post_turn_persist", **_persist_payload)

        log.info(
            "event_stream done: session=%s in=%d out=%d rounds=%d total=%.0fms agent_tool_calls=%d",
            self.session_id[:12], _input_tokens, _output_tokens,
            round_num, (time.monotonic() - _t0) * 1000, _agent_update_tool_calls,
        )
        yield {
            "type": "done",
            "text": final_text,
            "input_tokens": _input_tokens,
            "output_tokens": _output_tokens,
            "stop_reason": _last_stop_reason,
            "rounds_used": round_num,
        }

        # after_turn and trajectory run in the background — not on the critical path
        asyncio.create_task(self._background_finalize(
            user_input=user_input,
            final_text=final_text,
            workspace_tag=_workspace_tag,
            model=model,
            round_num=round_num,
            last_stop_reason=_last_stop_reason,
            traj_tool_calls=_traj_tool_calls,
            input_tokens=_input_tokens,
            output_tokens=_output_tokens,
        ))

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
        hook_bus = getattr(self, "hook_bus", None)
        if hook_bus is not None:
            try:
                import asyncio as _asyncio
                loop = _asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            payload = self._hook_payload(
                restored_turns=len(turns),
                used_summary=bool(summary),
            )
            if loop and loop.is_running():
                loop.create_task(hook_bus.emit("post_session_restore", **payload))
            else:
                _asyncio.run(hook_bus.emit("post_session_restore", **payload))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _credential_pool(self) -> list[str]:
        """Return the ordered credential pool from provider config."""
        return self.config.provider.credential_pool

    def _rotate_credential(self, current_index: int) -> tuple[bool, int]:
        """Try to rotate to the next credential in the pool.

        Returns (rotated: bool, next_index: int).
        rotated=True means the provider's active key was successfully changed.
        """
        pool = self._credential_pool()
        if len(pool) <= 1:
            return False, current_index
        next_index = (current_index + 1) % len(pool)
        new_key = pool[next_index]
        rotated = self.provider.rotate_credential(new_key)
        if rotated:
            log.info(
                "credential rotated: session=%s pool_size=%d index=%d→%d",
                self.session_id[:12], len(pool), current_index, next_index,
            )
        return rotated, next_index

    async def _call_provider(
        self,
        system: "str | tuple[str, str]",
        tools: list[dict] | None,
        model: str,
        max_retries: int = 2,
        _skip_pre_hook: bool = False,
    ) -> LLMResponse:
        """Call the provider with structured error recovery.

        On transient errors: exponential back-off retry.
        On rate-limit (429): try credential pool rotation before back-off.
        On context-length errors: compact context, then retry once.
        On auth failures / fatal errors: raise immediately.
        """
        complete_kwargs: dict = dict(
            messages=self._context,
            system=system,
            tools=tools,
            model=model,
        )
        if self.config.agent.max_tokens > 0:
            complete_kwargs["max_tokens"] = self.config.agent.max_tokens

        _t = time.monotonic()
        compress_attempted = False
        cred_index = 0  # tracks which pool slot is currently active
        for attempt in range(max_retries + 1):
            try:
                if not _skip_pre_hook or attempt > 0:
                    await self._emit_hook(
                        "pre_llm_call",
                        entrypoint="_call_provider",
                        system=system,
                        tools=tools,
                        messages=self._context,
                        active_model=model,
                        attempt=attempt + 1,
                    )
                response = await self.provider.complete(**complete_kwargs)
                self._total_input_tokens += response.input_tokens
                self._total_output_tokens += response.output_tokens
                await self._emit_hook(
                    "post_llm_call",
                    entrypoint="_call_provider",
                    active_model=model,
                    response=response,
                    response_text=response.content,
                    stop_reason=response.stop_reason,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    attempt=attempt + 1,
                )
                log.info(
                    "provider.reply: session=%s stop=%s content=%d tools=%d in=%d out=%d %.0fms",
                    self.session_id[:12], response.stop_reason, len(response.content or ""),
                    len(response.tool_calls or []), response.input_tokens, response.output_tokens,
                    (time.monotonic() - _t) * 1000,
                )
                return response
            except Exception as exc:
                recovery = classify_error(exc)
                log.warning("provider error (attempt %d): %s", attempt + 1, recovery.message)
                if recovery.is_auth_failure or not recovery.retryable:
                    raise
                if recovery.should_compress and not compress_attempted:
                    compress_attempted = True
                    policy = self._policy()
                    await self._compact_context(policy, cheap_model or model, reason="provider_recovery")
                    complete_kwargs["messages"] = self._context
                    # Don't count this as an "attempt" — retry immediately after compression
                    continue
                if attempt >= max_retries:
                    raise
                # On rate-limit, try rotating credential before sleeping.
                # If rotation succeeds, skip the back-off delay for this attempt.
                rotated, cred_index = self._rotate_credential(cred_index)
                if not rotated:
                    await asyncio.sleep(backoff(attempt))
        raise RuntimeError("unreachable")  # pragma: no cover

    def _append_assistant_message(self, response: LLMResponse) -> None:
        """Append the assistant's response to the context in API-compatible format."""
        if response.tool_calls:
            content_blocks: list[dict] = []
            if response.content:
                content_blocks.append({"type": "text", "text": response.content})
            for tc in response.tool_calls:
                block: dict = {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                if tc.thought_signature:
                    block["_thought_sig"] = tc.thought_signature
                content_blocks.append(block)
            self._context.append(Message(role="assistant", content=content_blocks))
        else:
            self._context.append(Message(role="assistant", content=response.content))


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

    async def _react_loop(
        self,
        system: "str | tuple[str, str]",
        tools: list[dict] | None,
        policy: ContextPolicy,
        *,
        user_input: str,
    ) -> LLMResponse:
        """Run the ReAct loop: call LLM, execute tools, repeat."""
        max_rounds = self.config.agent.max_tool_rounds
        model = self.config.agent.model
        cheap_model = self.config.agent.cheap_model or ""

        round_num = 0
        tool_names_this_turn: list[str] = []
        while True:
            if needs_compaction(self._context, policy):
                await self._compact_context(policy, cheap_model or model, reason="react_loop_budget")

            response = await self._call_provider(system, tools, model)
            self._append_assistant_message(response)

            if response.stop_reason != "tool_use" or not response.tool_calls:
                break
            if max_rounds > 0 and round_num >= max_rounds:
                log.warning("Max tool rounds (%d) reached", max_rounds)
                break

            for tc in response.tool_calls:
                tool_names_this_turn.append(tc.name)
                await self._emit_hook(
                    "pre_tool_call",
                    entrypoint="react_loop",
                    tool_name=tc.name,
                    tool_input=tc.input,
                    call_id=tc.id,
                )
                result = await self.executor.execute(tc.name, tc.input)
                await self._emit_hook(
                    "post_tool_call",
                    entrypoint="react_loop",
                    tool_name=tc.name,
                    tool_input=tc.input,
                    tool_result=result.content,
                    is_error=result.is_error,
                    call_id=tc.id,
                )
                self.memory.save_turn(self.session_id, "tool", result.content, tool_name=tc.name)
                self._context.append(Message(
                    role="tool", content=result.content,
                    tool_call_id=tc.id, tool_name=tc.name,
                ))
            round_num += 1

        if not response.content and self._memory_only_tool_names(tool_names_this_turn):
            recovery = await self._force_user_facing_answer(system, user_input)
            if recovery.content:
                self._append_assistant_message(recovery)
                return recovery

        return response

        return LLMResponse(content="", stop_reason="end_turn")  # unreachable
