"""Context assembly service."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from hushclaw.context.policy import ContextPolicy
from hushclaw.prompts import (
    SECTION_AGENT_INSTRUCTIONS,
    SECTION_BELIEF_MODELS,
    SECTION_INSTRUCTIONS,
    SECTION_RANDOM_MEMORIES,
    SECTION_RECALLED_MEMORIES,
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

_RECALL_HISTORY_RE = re.compile(
    r"(?:之前|上次|还记得|记不记得|我们决定|你知道我|按我的习惯|延续之前|"
    r"before|earlier|last time|remember|we decided|my preference|my preferences|"
    r"my usual|based on what we discussed)",
    re.IGNORECASE,
)
_RECALL_SEMANTIC_RE = re.compile(
    r"(?:为什么|怎么|如何|什么|原因|背景|总结|结论|方案|偏好|习惯|约定|决策|"
    r"why|how|what|summary|summarize|decision|decisions|preference|preferences|"
    r"conclusion|conclusions|background|context|convention|conventions)",
    re.IGNORECASE,
)
_OPERATIONAL_QUERY_RE = re.compile(
    r"^(?:继续|好的|好|行|修一下|改一下|跑测试|测试|提交|提交一下|"
    r"继续做|继续改|看一下|处理一下|优化一下|重试|"
    r"continue|ok|okay|fix(?: it| this)?|run tests?|test(?: it)?|commit|"
    r"retry|ship it|take a look|check it)$",
    re.IGNORECASE,
)
_SYNTHESIS_QUERY_RE = re.compile(
    r"(?:整理一下|系统梳理|梳理一下|总结一下|汇总一下|收一下|归纳一下|形成方案|"
    r"给我(?:一版|一个)?(?:系统)?梳理|输出方案|定稿|收敛一下|"
    r"summar(?:ize|ise)|synthesis|synthesize|wrap(?: |-)?up|organi[sz]e(?: this)?|"
    r"pull it together|final(?:ize)?|write (?:it )?up)",
    re.IGNORECASE,
)
_DISCUSSION_CUE_RE = re.compile(
    r"^(?:我觉得|我认为|我倾向于|我的看法是|再补充一点|补充一点|另外|还有一点|"
    r"我在想|我有个想法|先讨论一下|先别总结|先聊聊|"
    r"i think|i feel|my view is|one more thing|another point|"
    r"i'm thinking|let's discuss|not final yet|don't summarize yet)",
    re.IGNORECASE,
)
_DIRECT_ASK_RE = re.compile(
    r"(?:\?|？|请问|能否|可以.*吗|是否|what|why|how|can you|could you|would you)",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    return len(parts)


def _looks_like_short_operational_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if _OPERATIONAL_QUERY_RE.match(q):
        return True
    # For CJK text without spaces, character count is a better proxy than word
    # count. Keep this threshold tight so normal viewpoint statements are not
    # misclassified as operational nudges like "继续" or "改一下".
    if not re.search(r"\s", q) and re.search(r"[\u4e00-\u9fff]", q):
        return len(q) <= 8
    if len(q) <= 12:
        return True
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
    if _RECALL_HISTORY_RE.search(q):
        return True
    if not has_working_state:
        return True
    if _looks_like_short_operational_query(q):
        return False
    if _RECALL_SEMANTIC_RE.search(q):
        return True
    return len(q) >= 48 or _word_count(q) >= 8


def detect_response_mode(
    query: str,
    *,
    has_working_state: bool,
) -> str:
    """Classify this turn's desired response style.

    Returns one of:
      - ``discussion``: light conversational response, avoid over-structuring
      - ``synthesis``: explicit request to consolidate prior discussion
      - ``default``: ordinary answer behavior
    """
    q = (query or "").strip()
    if not q:
        return "default"
    if _SYNTHESIS_QUERY_RE.search(q):
        return "synthesis"
    if _looks_like_short_operational_query(q):
        return "default"

    has_direct_ask = bool(_DIRECT_ASK_RE.search(q))
    looks_discussion = bool(_DISCUSSION_CUE_RE.search(q))
    long_statement = len(q) >= 24 and _word_count(q) >= 6

    if looks_discussion and not has_direct_ask:
        return "discussion"
    if has_working_state and long_statement and not has_direct_ask:
        return "discussion"
    return "default"


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
        profile_cache_ttl: float = 5.0,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._read_file_cached = read_file_cached
        self._resolve_effective_timezone = resolve_effective_timezone
        self._build_relative_day_anchors = build_relative_day_anchors
        self._profile_cache_ttl = profile_cache_ttl
        self._profile_cache: tuple[str, float] | None = None
        self._ws_cache: dict[str, tuple[str | None, float]] = {}

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
    ) -> tuple[str, str]:
        workspace_dir = workspace_dir_override if workspace_dir_override is not None else self._workspace_dir
        stable = self._build_stable_prefix(config, workspace_dir)
        dynamic = self._build_dynamic_suffix(
            query,
            policy,
            memory,
            config,
            stable=stable,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
            workspace_dir=workspace_dir,
            workspace_dir_override=workspace_dir_override,
            references=references or [],
        )
        return stable, dynamic

    def _build_stable_prefix(
        self,
        config: "AgentConfig",
        workspace_dir: Path | None,
    ) -> str:
        stable = config.system_prompt.replace(" Today is {date}.", "").replace("Today is {date}.", "")

        agents_injected = False
        if workspace_dir:
            agents_path = workspace_dir / "AGENTS.md"
            if agents_path.is_file():
                agents_text = self._read_file_cached(agents_path)
                if agents_text:
                    stable += f"\n\n{SECTION_AGENT_INSTRUCTIONS}\n{agents_text}"
                    agents_injected = True
        if not agents_injected and config.instructions:
            stable += f"\n\n{SECTION_INSTRUCTIONS}\n{config.instructions}"

        if getattr(config, "html_render_hint", True):
            stable += (
                "\n\n## Output Format — Rich HTML\n"
                "Only output a ```html fenced code block when the user explicitly asks for an HTML "
                "visualization, chart, diagram, or interactive component. "
                "For all other responses — including tables, data summaries, and analysis — use "
                "plain Markdown. Do not proactively choose HTML over Markdown."
            )

        if workspace_dir:
            soul_path = workspace_dir / "SOUL.md"
            if soul_path.is_file():
                soul_text = self._read_file_cached(soul_path)
                if soul_text:
                    stable += f"\n\n{SECTION_WORKSPACE_IDENTITY}\n{soul_text}"

        return stable

    def _build_dynamic_suffix(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        *,
        stable: str,
        session_id: str | None,
        pipeline_run_id: str,
        workspace_dir: Path | None,
        workspace_dir_override: Path | None,
        references: list[dict],
    ) -> str:
        tz_obj, tz_name = self._resolve_effective_timezone()
        now_local = datetime.now(tz_obj)
        anchors = self._build_relative_day_anchors(now_local)
        dynamic_parts = [f"Today is {anchors['today_date']}."]

        if workspace_dir:
            user_path = workspace_dir / "USER.md"
            if user_path.is_file():
                user_text = self._read_file_cached(user_path)
                if user_text:
                    dynamic_parts.append(f"{SECTION_USER_NOTES}\n{user_text}")

        recall_scopes = self._build_recall_scopes(config, workspace_dir_override, pipeline_run_id)

        profile_snapshot = self._load_profile_snapshot(memory)
        if profile_snapshot:
            dynamic_parts.append(f"{SECTION_USER_PROFILE}\n{profile_snapshot}")

        belief_models_text = memory.render_belief_models(
            scopes=recall_scopes,
            query=query,
            max_chars=700,
            max_models=3,
        )
        if belief_models_text:
            dynamic_parts.append(f"{SECTION_BELIEF_MODELS}\n{belief_models_text}")

        working_state = self._load_working_state(memory, session_id)
        if working_state:
            dynamic_parts.append(f"{SECTION_WORKING_STATE}\n{working_state}")

        referenced_messages = self._render_referenced_messages(
            memory,
            references,
            policy,
            session_id=session_id or "",
        )
        if referenced_messages:
            dynamic_parts.append(f"## Referenced Messages\n{referenced_messages}")

        response_mode = detect_response_mode(
            query,
            has_working_state=bool(working_state),
        )
        if response_mode == "discussion":
            dynamic_parts.append(
                "[RESPONSE MODE] Discussion mode. "
                "The user appears to be thinking aloud or iterating on ideas rather than asking for a final deliverable. "
                "Reply briefly and conversationally. "
                "Do not over-structure, do not prematurely summarize the whole discussion, "
                "and do not turn every turn into a long essay. "
                "Focus on the most useful reaction, tension, or clarification that moves the discussion forward."
            )
        elif response_mode == "synthesis":
            dynamic_parts.append(
                "[RESPONSE MODE] Synthesis mode. "
                "The user is explicitly asking for a structured consolidation. "
                "Pull together the discussion into a clear organized response. "
                "Surface decisions, tradeoffs, open questions, and recommended next steps when relevant."
            )

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

        dynamic_parts.append(
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

        response_language = detect_response_language(query)
        if response_language:
            dynamic_parts.append(f"[LANG] Reply to the user in {_LANG_NAMES[response_language]}.")

        dynamic = "\n\n".join(dynamic_parts)
        log.info(
            "assemble: session=%s recall=%s %.0fms(%s) serendipity=%.0fms stable=%d dynamic=%d",
            (session_id or "?")[:12],
            "on" if auto_recall else "off",
            recall_ms,
            "hit" if memories_text else "miss",
            rand_ms,
            len(stable),
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
        return working_state

    @staticmethod
    def _split_memory_budgets(policy: ContextPolicy) -> tuple[int, int]:
        serendipity = max(0.0, min(1.0, policy.serendipity_budget))
        if serendipity > 0.0:
            random_budget = int(policy.memory_max_tokens * serendipity)
            return policy.memory_max_tokens - random_budget, random_budget
        return policy.memory_max_tokens, 0
