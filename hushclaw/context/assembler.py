"""Context assembly service."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from hushclaw.context.policy import ContextPolicy
from hushclaw.context.session_recall import SessionRecall, should_session_recall
from hushclaw.context.trace import ContextTrace
from hushclaw.prompt_blocks import (
    PromptAssembler,
    PromptBlock,
    PromptBlockRegistry,
    PromptRenderContext,
    legacy_system_prompt_block,
)
from hushclaw.prompts import (
    SECTION_AGENT_INSTRUCTIONS,
    SECTION_BELIEF_MODELS,
    SECTION_INSTRUCTIONS,
    SECTION_RANDOM_MEMORIES,
    SECTION_RECALLED_MEMORIES,
    SECTION_SESSION_RECALL,
    SECTION_USER_NOTES,
    SECTION_USER_PROFILE,
    SECTION_WORKING_STATE,
    SECTION_WORKSPACE_IDENTITY,
)
from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.config.schema import AgentConfig
    from hushclaw.memory.store import MemoryStore

log = get_logger("context.assembler")

_LANG_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}

def _word_count(text: str) -> int:
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    return len(parts)


def _looks_like_short_operational_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if not re.search(r"\s", q) and re.search(r"[\u4e00-\u9fff]", q):
        return len(q) <= 8
    return len(q) <= 24 and _word_count(q) <= 4


def should_auto_recall(
    query: str,
    *,
    has_working_state: bool,
    pipeline_run_id: str = "",
) -> bool:
    """Decide whether this turn should auto-inject long-term memories."""
    q = (query or "").strip()
    if not q:
        return False
    if pipeline_run_id:
        return True
    if not has_working_state:
        return True
    if _looks_like_short_operational_query(q):
        return False
    return len(q) >= 48 or _word_count(q) >= 8


def detect_response_language(text: str) -> str | None:
    """Return ISO code if the text is non-English, else None."""
    if not text:
        return None
    sample = text[:300]
    n = max(len(sample), 1)
    if sum(1 for c in sample if "\u4e00" <= c <= "\u9fff") / n > 0.12:
        return "zh"
    if sum(1 for c in sample if "\u3040" <= c <= "\u30ff") / n > 0.08:
        return "ja"
    if sum(1 for c in sample if "\uac00" <= c <= "\ud7af") / n > 0.08:
        return "ko"
    return None


class ContextAssembler:
    """Build the stable and dynamic prompt sections for one turn."""

    def __init__(
        self,
        *,
        workspace_dir: Path | None = None,
        read_file_cached: Callable[[Path], str | None],
        resolve_effective_timezone: Callable[[], tuple[object, str]],
        build_relative_day_anchors: Callable[[datetime], dict[str, str]],
        prompt_blocks: PromptBlockRegistry | None = None,
        profile_cache_ttl: float = 5.0,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._read_file_cached = read_file_cached
        self._resolve_effective_timezone = resolve_effective_timezone
        self._build_relative_day_anchors = build_relative_day_anchors
        self._prompt_blocks = prompt_blocks
        self._profile_cache_ttl = profile_cache_ttl
        self._profile_cache: tuple[str, float] | None = None
        self._ws_cache: dict[str, tuple[str | None, float]] = {}
        self._trace = ContextTrace()

    def context_trace(self) -> dict:
        return self._trace.summary()

    async def assemble(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        *,
        session_id: str | None = None,
        pipeline_run_id: str = "",
        workspace_dir_override: Path | None = None,
        references: list[dict] | None = None,
        prompt_context: PromptRenderContext | None = None,
    ) -> tuple[str, str]:
        self._trace.reset()
        workspace_dir = workspace_dir_override if workspace_dir_override is not None else self._workspace_dir
        render_context = replace(
            prompt_context or PromptRenderContext(),
            config=config,
            memory=memory,
            query=query,
            session_id=session_id or "",
            workspace_dir=workspace_dir,
            model=getattr(config, "model", ""),
        )
        stable_runtime = self._build_stable_runtime(config, workspace_dir)
        # Recall combines SQLite FTS, local vector scoring, and workspace I/O.
        # Run it off the event loop so one slow session does not stall streaming,
        # heartbeats, or other concurrent sessions in the same process.
        dynamic_runtime = await asyncio.to_thread(
            self._build_dynamic_suffix,
            query,
            policy,
            memory,
            config,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
            workspace_dir=workspace_dir,
            workspace_dir_override=workspace_dir_override,
            references=references or [],
        )
        registry = self._prompt_blocks.copy() if self._prompt_blocks is not None else PromptBlockRegistry([
            legacy_system_prompt_block(config.system_prompt),
        ])
        if stable_runtime:
            registry.register(PromptBlock(
                id="runtime.workspace_context",
                owner="user",
                tier="stable",
                priority=900,
                cacheable=True,
                title="Workspace Context",
                content=stable_runtime,
            ))
        tool_surface_hint = str(render_context.extra.get("tool_surface_hint") or "").strip()
        if tool_surface_hint:
            registry.register(PromptBlock(
                id="runtime.tool_surface",
                owner="kernel",
                tier="stable",
                priority=850,
                cacheable=True,
                title="Tool Surface",
                content=tool_surface_hint,
            ))
            self._trace.add("tool_surface", tier="stable", content=tool_surface_hint)
        if dynamic_runtime:
            registry.register(PromptBlock(
                id="runtime.turn_context",
                owner="kernel",
                tier="dynamic",
                priority=900,
                cacheable=False,
                title="Turn Context",
                content=dynamic_runtime,
            ))
        strategy_hint = str(render_context.extra.get("strategy_hint") or "").strip()
        if strategy_hint:
            registry.register(PromptBlock(
                id="runtime.strategy_hint",
                owner="kernel",
                tier="dynamic",
                priority=950,
                cacheable=False,
                title="Prior Workflow Hint",
                content=strategy_hint,
            ))
            self._trace.add("strategy_hint", tier="dynamic", content=strategy_hint)
        started = time.time()
        assembly = PromptAssembler(registry).assemble(render_context)
        self._trace.set_prompt_manifest(assembly.manifest_dict())
        self._trace.add(
            "prompt_blocks",
            tier="stable",
            content=assembly.stable,
            elapsed_ms=(time.time() - started) * 1000,
            metadata={"mode": "registry", "blocks": len(assembly.manifest)},
        )
        log.info(
            "prompt assembled: session=%s stable=%d dynamic=%d blocks=%d",
            (session_id or "?")[:12],
            len(assembly.stable),
            len(assembly.dynamic),
            len(assembly.manifest),
        )
        return assembly.stable, assembly.dynamic

    def _build_stable_runtime(
        self,
        config: "AgentConfig",
        workspace_dir: Path | None,
    ) -> str:
        stable = ""

        agents_injected = False
        if workspace_dir:
            agents_path = workspace_dir / "AGENTS.md"
            if agents_path.is_file():
                agents_text = self._read_file_cached(agents_path)
                if agents_text:
                    stable += f"\n\n{SECTION_AGENT_INSTRUCTIONS}\n{agents_text}"
                    self._trace.add("workspace_agents", tier="stable", content=agents_text)
                    agents_injected = True
        if not agents_injected and config.instructions:
            stable += f"\n\n{SECTION_INSTRUCTIONS}\n{config.instructions}"
            self._trace.add("agent_instructions", tier="stable", content=config.instructions)
        elif not agents_injected:
            self._trace.add("agent_instructions", tier="stable", hit=False)

        if getattr(config, "html_render_hint", True):
            html_hint = (
                "Only output a ```html fenced code block when the user explicitly asks for an HTML "
                "visualization, chart, diagram, or interactive component. "
                "For all other responses — including tables, data summaries, and analysis — use "
                "plain Markdown. Do not proactively choose HTML over Markdown."
            )
            stable += (
                "\n\n## Output Format — Rich HTML\n"
                f"{html_hint}"
            )
            self._trace.add("html_render_hint", tier="stable", content=html_hint)
        else:
            self._trace.add("html_render_hint", tier="stable", hit=False)

        if workspace_dir:
            soul_path = workspace_dir / "SOUL.md"
            if soul_path.is_file():
                soul_text = self._read_file_cached(soul_path)
                if soul_text:
                    stable += f"\n\n{SECTION_WORKSPACE_IDENTITY}\n{soul_text}"
                    self._trace.add("workspace_identity", tier="stable", content=soul_text)
        else:
            self._trace.add("workspace_identity", tier="stable", hit=False)

        return stable

    def _build_dynamic_suffix(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        *,
        session_id: str | None,
        pipeline_run_id: str,
        workspace_dir: Path | None,
        workspace_dir_override: Path | None,
        references: list[dict],
    ) -> str:
        tz_obj, tz_name = self._resolve_effective_timezone()
        now_local = datetime.now(tz_obj)
        anchors = self._build_relative_day_anchors(now_local)
        date_text = f"Today is {anchors['today_date']}."
        dynamic_parts = [date_text]
        self._trace.add("date", tier="dynamic", content=date_text)

        if workspace_dir:
            user_path = workspace_dir / "USER.md"
            if user_path.is_file():
                user_text = self._read_file_cached(user_path)
                if user_text:
                    dynamic_parts.append(f"{SECTION_USER_NOTES}\n{user_text}")
                    self._trace.add("user_notes", tier="dynamic", content=user_text)
                else:
                    self._trace.add("user_notes", tier="dynamic", hit=False)
            else:
                self._trace.add("user_notes", tier="dynamic", hit=False)
        else:
            self._trace.add("user_notes", tier="dynamic", hit=False)

        recall_scopes = self._build_recall_scopes(config, workspace_dir_override, pipeline_run_id)

        profile_started = time.time()
        profile_snapshot = self._load_profile_snapshot(memory)
        self._trace.add(
            "user_profile",
            tier="dynamic",
            content=profile_snapshot or "",
            elapsed_ms=(time.time() - profile_started) * 1000,
            metadata={"source": "user_profile.render_profile_context", "max_chars": 1000},
        )
        if profile_snapshot:
            dynamic_parts.append(f"{SECTION_USER_PROFILE}\n{profile_snapshot}")

        belief_started = time.time()
        belief_models_text = memory.render_belief_models(
            scopes=recall_scopes,
            query=query,
            max_chars=700,
            max_models=3,
        )
        self._trace.add(
            "belief_models",
            tier="dynamic",
            content=belief_models_text or "",
            budget_tokens=175,
            elapsed_ms=(time.time() - belief_started) * 1000,
            metadata={"scopes": recall_scopes or [], "query_aware": bool(query), "max_models": 3},
        )
        if belief_models_text:
            dynamic_parts.append(f"{SECTION_BELIEF_MODELS}\n{belief_models_text}")

        working_state_started = time.time()
        working_state = self._load_working_state(memory, session_id)
        self._trace.add(
            "working_state",
            tier="dynamic",
            content=working_state or "",
            elapsed_ms=(time.time() - working_state_started) * 1000,
        )
        if working_state:
            dynamic_parts.append(f"{SECTION_WORKING_STATE}\n{working_state}")

        references_started = time.time()
        referenced_messages = self._render_referenced_messages(
            memory,
            references,
            policy,
            session_id=session_id or "",
        )
        self._trace.add(
            "referenced_messages",
            tier="dynamic",
            content=referenced_messages or "",
            budget_tokens=policy.reference_max_tokens,
            elapsed_ms=(time.time() - references_started) * 1000,
            metadata={"requested": len(references or [])},
        )
        if referenced_messages:
            dynamic_parts.append(f"## Referenced Messages\n{referenced_messages}")

        session_recall_text = ""
        session_recall_ms = 0.0
        should_recall_sessions = should_session_recall(
            query,
            has_working_state=bool(working_state),
            min_query_chars=policy.session_recall_min_query_chars,
        )
        if should_recall_sessions:
            session_recall_started = time.time()
            session_recall = SessionRecall(memory).recall(
                query,
                current_session_id=session_id or "",
                workspace=workspace_dir_override.name if workspace_dir_override is not None else "",
                max_tokens=policy.session_recall_max_tokens,
                limit=policy.session_recall_limit,
            )
            session_recall_ms = (time.time() - session_recall_started) * 1000
            session_recall_text = session_recall.text
        self._trace.add(
            "session_recall",
            tier="dynamic",
            content=session_recall_text or "",
            hit=bool(session_recall_text),
            budget_tokens=policy.session_recall_max_tokens,
            elapsed_ms=session_recall_ms,
            metadata={
                "enabled": should_recall_sessions,
                "limit": policy.session_recall_limit,
                "has_working_state": bool(working_state),
                "min_query_chars": policy.session_recall_min_query_chars,
            },
        )
        if session_recall_text:
            dynamic_parts.append(f"{SECTION_SESSION_RECALL}\n{session_recall_text}")

        main_budget, random_budget = self._split_memory_budgets(policy)
        auto_recall = should_auto_recall(
            query,
            has_working_state=bool(working_state),
            pipeline_run_id=pipeline_run_id,
        )

        memories_text = ""
        if auto_recall:
            recall_started = time.time()
            memories_text = memory.recall_with_budget(
                query,
                min_score=policy.memory_min_score,
                max_tokens=main_budget,
                session_id=session_id,
                decay_rate=policy.memory_decay_rate,
                retrieval_temperature=policy.retrieval_temperature,
                scopes=recall_scopes,
                max_age_days=policy.max_age_days,
                exclude_types={"action_log"},
            )
            recall_ms = (time.time() - recall_started) * 1000
        else:
            recall_ms = 0.0
        self._trace.add(
            "memory_recall",
            tier="dynamic",
            content=memories_text or "",
            hit=bool(memories_text),
            budget_tokens=main_budget,
            elapsed_ms=recall_ms,
            metadata={
                "enabled": auto_recall,
                "scopes": recall_scopes or [],
                "has_working_state": bool(working_state),
                "min_score": policy.memory_min_score,
                "exclude_types": ["action_log"],
            },
        )
        if memories_text:
            dynamic_parts.append(f"{SECTION_RECALLED_MEMORIES}\n{memories_text}")

        if random_budget > 0:
            rand_started = time.time()
            random_memories = memory.recall_with_budget(
                "",
                min_score=0.1,
                max_tokens=random_budget,
                retrieval_temperature=1.0,
                scopes=recall_scopes,
                exclude_types={"action_log"},
            )
            rand_ms = (time.time() - rand_started) * 1000
            if random_memories:
                dynamic_parts.append(f"{SECTION_RANDOM_MEMORIES}\n{random_memories}")
        else:
            rand_ms = 0.0
            random_memories = ""
        self._trace.add(
            "random_memories",
            tier="dynamic",
            content=random_memories or "",
            hit=bool(random_memories),
            budget_tokens=random_budget,
            elapsed_ms=rand_ms,
            metadata={"enabled": random_budget > 0},
        )

        timezone_text = (
            f"[TZ] User's timezone: {tz_name}. "
            f"Interpret relative times ('2 PM', 'tomorrow morning') in this timezone. "
            f"Store datetimes as UTC with Z suffix, e.g. '2026-04-22T09:00:00Z'. "
            f"Relative day anchors: "
            f"yesterday={anchors['yesterday_date']} "
            f"(from_time=\"{anchors['yesterday_from_utc']}\" to_time=\"{anchors['yesterday_to_utc']}\"), "
            f"today={anchors['today_date']} "
            f"(from_time=\"{anchors['today_from_utc']}\" to_time=\"{anchors['today_to_utc']}\"), "
            f"tomorrow={anchors['tomorrow_date']} "
            f"(from_time=\"{anchors['tomorrow_from_utc']}\" to_time=\"{anchors['tomorrow_to_utc']}\")."
        )
        dynamic_parts.append(timezone_text)
        self._trace.add("timezone", tier="dynamic", content=timezone_text, metadata={"timezone": tz_name})

        response_language = detect_response_language(query)
        if response_language:
            language_text = f"[LANG] Reply to the user in {_LANG_NAMES[response_language]}."
            dynamic_parts.append(language_text)
            self._trace.add("language", tier="dynamic", content=language_text, metadata={"language": response_language})
        else:
            self._trace.add("language", tier="dynamic", hit=False)

        dynamic = "\n\n".join(dynamic_parts)
        log.info(
            "context resolved: session=%s session_recall=%s %.0fms recall=%s %.0fms(%s) serendipity=%.0fms dynamic=%d",
            (session_id or "?")[:12],
            "hit" if session_recall_text else ("miss" if should_recall_sessions else "off"),
            session_recall_ms,
            "on" if auto_recall else "off",
            recall_ms,
            "hit" if memories_text else "miss",
            rand_ms,
            len(dynamic),
        )
        return dynamic

    def _render_referenced_messages(
        self,
        memory: "MemoryStore",
        references: list[dict],
        policy: ContextPolicy,
        *,
        session_id: str,
    ) -> str:
        if not references:
            return ""
        max_items = max(0, int(policy.reference_max_items or 0))
        max_tokens = max(0, int(policy.reference_max_tokens or 0))
        per_item_tokens = max(1, int(policy.reference_item_max_tokens or 1))
        if max_items <= 0 or max_tokens <= 0:
            return ""

        total_chars_budget = max_tokens * 4
        per_item_chars = per_item_tokens * 4
        rendered: list[str] = []
        used_chars = 0
        requested = len(references)
        truncated = 0

        seen: set[str] = set()
        for ref in references[:max_items]:
            mid = ""
            if isinstance(ref, dict):
                mid = str(ref.get("message_id") or "").strip()
            else:
                mid = str(ref or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            resolved = memory.resolve_message_ref(mid, session_id=session_id)
            if not resolved:
                continue
            role = str(resolved.get("role") or "message")
            ts = str(resolved.get("ts") or "")
            text = " ".join(str(resolved.get("content") or "").split())
            if not text:
                continue
            remaining = total_chars_budget - used_chars
            if remaining <= 0:
                truncated += 1
                break
            item_budget = min(per_item_chars, remaining)
            clipped = text[:item_budget].rstrip()
            if len(text) > len(clipped):
                clipped += "\n[truncated]"
                truncated += 1
            block = f"[{resolved.get('message_id', mid)}][{role}][{ts}]\n{clipped}"
            rendered.append(block)
            used_chars += len(clipped)

        if references and (requested > len(rendered)):
            truncated += max(0, requested - max_items)
        if rendered:
            log.info(
                "assemble references: session=%s requested=%d included=%d truncated=%d chars=%d",
                session_id[:12] if session_id else "?",
                requested,
                len(rendered),
                truncated,
                used_chars,
            )
        return "\n\n".join(rendered)

    def _build_recall_scopes(
        self,
        config: "AgentConfig",
        workspace_dir_override: Path | None,
        pipeline_run_id: str,
    ) -> list[str] | None:
        memory_scope = config.memory_scope
        recall_scopes: list[str] | None = ["global", f"agent:{memory_scope}"] if memory_scope else None
        if workspace_dir_override is not None:
            recall_scopes = (recall_scopes or ["global"]) + [f"workspace:{workspace_dir_override.name}"]
        if pipeline_run_id:
            recall_scopes = (recall_scopes or ["global"]) + [f"pipeline:{pipeline_run_id}"]
        return recall_scopes

    def _load_profile_snapshot(self, memory: "MemoryStore") -> str:
        now = time.time()
        if self._profile_cache is None or now - self._profile_cache[1] >= self._profile_cache_ttl:
            self._profile_cache = (memory.user_profile.render_profile_context(max_chars=1000), now)
        return self._profile_cache[0]

    def _load_working_state(self, memory: "MemoryStore", session_id: str | None) -> str | None:
        if not session_id:
            return None

        ws_path = memory.sessions_dir / session_id / "working_state.md"
        try:
            ws_mtime = ws_path.stat().st_mtime
        except OSError:
            ws_mtime = 0.0

        cached_ws = self._ws_cache.get(session_id)
        if cached_ws is not None and cached_ws[1] == ws_mtime:
            return cached_ws[0]

        working_state = memory.load_session_working_state(session_id)
        # Evict oldest entry if cache is full (simple LRU-lite).
        if len(self._ws_cache) >= 128:
            oldest = next(iter(self._ws_cache))
            del self._ws_cache[oldest]
        self._ws_cache[session_id] = (working_state, ws_mtime)

        global_state = getattr(memory, "load_global_working_state", lambda: None)()
        parts = []
        if global_state:
            parts.append(f"[Persistent goals]\n{global_state}")
        if working_state:
            parts.append(f"[Session context]\n{working_state}")
        combined = "\n\n".join(parts) if parts else None
        return combined

    @staticmethod
    def _split_memory_budgets(policy: ContextPolicy) -> tuple[int, int]:
        serendipity = max(0.0, min(1.0, policy.serendipity_budget))
        if serendipity > 0.0:
            random_budget = int(policy.memory_max_tokens * serendipity)
            return policy.memory_max_tokens - random_budget, random_budget
        return policy.memory_max_tokens, 0
