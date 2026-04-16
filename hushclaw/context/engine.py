"""ContextEngine ABC and DefaultContextEngine implementation."""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from hushclaw.context.policy import ContextPolicy
from hushclaw.prompts import (
    COMPACT_ABSTRACTIVE_TEMPLATE,
    COMPACT_LOSSLESS_TEMPLATE,
    COMPACT_SUMMARY_PREFIX,
    COMPACT_SYSTEM,
    SECTION_AGENT_INSTRUCTIONS,
    SECTION_INSTRUCTIONS,
    SECTION_RANDOM_MEMORIES,
    SECTION_RECALLED_MEMORIES,
    SECTION_USER_NOTES,
    SECTION_WORKING_STATE,
    SECTION_WORKSPACE_IDENTITY,
)
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
#
# Root-cause hardening:
# - User channel: semantic fact patterns
# - Assistant channel: artifacts only (URL/path/version)
_AUTO_EXTRACT_USER_PATTERNS = [
    # Names / identities
    r"(?:我叫|名字是|my name is|I(?:'m| am) called)\s+(\S+)",
    # Project names (Chinese and English)
    r"(?:项目名|项目|project(?:\s+name)?)\s*[：:=]\s*(.+?)(?:\s*[,，\n]|$)",
    # Task / goal statements (Chinese)
    r"(?:目标|需求|任务|我想要|我需要|帮我|请帮)\s*[：:是]?\s*(.{10,80}?)(?:[。\n]|$)",
    # Conclusions / decisions (Chinese)
    r"(?:决定|结论|方案|选择了|最终|我们采用|确定使用)\s*[：:是]?\s*(.{8,80}?)(?:[。\n]|$)",
    # User preferences (Chinese)
    r"(?:用户偏好|偏好|习惯|我喜欢|我不喜欢|风格)\s*[：:是]?\s*(.{8,80}?)(?:[。\n]|$)",
    # Key decisions (English) — avoid bare "using …" (matches normal prose / markdown)
    r"(?:decided to|we chose|the approach is|using\s*:|I prefer|key decision)\s*[：:\s]?\s*(.{8,100}?)(?:[.\n]|$)",
]
_AUTO_EXTRACT_ASSISTANT_PATTERNS = [
    # URLs
    r"https?://[^\s\"'>]+",
    # Unix/Windows file paths (e.g. /Users/foo/bar.pptx or ~/Desktop/foo.pdf)
    r"(?:^|\s|[：:=\(])(~?/(?:[\w.% -]+/)+[\w.% -]+\.[\w]+)",
    # Version strings
    r"\bv\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?\b",
]

# Typed extraction patterns — assign note_type at extraction time.
# Prioritized before the generic user patterns in after_turn().
_INTEREST_PATTERNS = [
    # Questions revealing what the user cares about
    r"(?:为什么|怎么|如何|什么是|有没有|是否|能否|会不会)\s+(.{8,80}?)(?:[?？。\n]|$)",
    r"(?:why (?:does|is|would|did)|how (?:does|can|to|do)|what is|is there)\s+(.{8,100}?)(?:[?.\n]|$)",
]
_BELIEF_PATTERNS = [
    # Opinions and principles the user expresses
    r"(?:我认为|我觉得|我感觉|应该|不应该|这应该|这不应该)\s+(.{8,80}?)(?:[。\n]|$)",
    r"(?:I think|I believe|I feel|should(?:n't| not)|this should(?:n't| not))\s+(.{8,100}?)(?:[.\n]|$)",
]
# Suppress auto-extraction of action-log-like text from user messages.
_ACTION_LOG_RE = re.compile(
    r"^(?:帮我|帮助我|已(?:修复|完成|创建|删除|更改|修改)|完成了|修改了|创建了|删除了|"
    r"updated|fixed|completed|created|deleted|I(?:'ve| have) (?:fixed|updated|created|deleted|completed))\s+.{5,}",
    re.IGNORECASE,
)

# Max auto-extracted memories per turn (keep small to reduce low-value noise).
_AUTO_EXTRACT_MAX_PER_TURN = 3
_AUTO_EXTRACT_STOP_PHRASES = (
    "保存到记忆",
    "并保存到记忆",
    "已保存到记忆",
    "save to memory",
    "saved to memory",
)

# Correction signal patterns: user negatively evaluates the assistant's last response.
# Matched against user_input only (not assistant output).
_CORRECTION_RE = re.compile(
    r"不对|不是这样|理解错了|你弄错了|重新来|再来一次|不是我要的|不是我想要|"
    r"错了|搞错了|没理解|不符合|不对劲|不是这个意思|"
    r"that'?s (not right|wrong|incorrect)|you misunderstood|"
    r"try again|not what I (want|meant|asked)|"
    r"incorrect|you got it wrong|wrong answer",
    re.IGNORECASE,
)
_AUTO_EXTRACT_FRAGMENT_PREFIXES = (
    "并", "以及", "并且", "另外", "然后", "且", "并将",
    "and ", "then ",
)
_AUTO_EXTRACT_PATH_RE = re.compile(r"^~?/(?:[\w.% -]+/)+[\w.% -]+\.[\w]+$")
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


def _strip_markdown_noise(s: str) -> str:
    """Remove common markdown / list debris from regex capture groups."""
    t = s.strip()
    t = re.sub(r"\*+", "", t)
    t = re.sub(r'^[`#>\s》」』"\']+|[`#>\s》」』"\']+$', "", t)
    return t.strip(" \t\r\n。，、,.;；:：\"'（）()[]【】「」『』-*_")


def _auto_extract_fact_ok(fact: str) -> bool:
    """Drop fragmented or punctuation-heavy captures (e.g. '** PPT。' from model output)."""
    t = _strip_markdown_noise(fact)
    lower_t = t.lower()
    if any(p in t or p in lower_t for p in _AUTO_EXTRACT_STOP_PHRASES):
        return False
    if len(t) < 8:
        return False
    # Need enough letters, digits, or CJK — not mostly symbols
    substantive = re.findall(r"[\w\u4e00-\u9fff]", t)
    if len(substantive) < 4:
        return False
    if len(substantive) / max(len(t), 1) < 0.45:
        return False
    if t.startswith(_AUTO_EXTRACT_FRAGMENT_PREFIXES):
        return False
    if t.endswith((",", "，", ";", "；", ":", "：", '"', "'")):
        return False
    return True


def _extract_from_text(
    text: str,
    patterns: list[str],
    *,
    artifact_only: bool,
    seen: set[str],
    out: list[str],
    limit: int,
) -> None:
    if not text or len(out) >= limit:
        return
    for pattern in patterns:
        if len(out) >= limit:
            break
        for match in re.findall(pattern, text, re.IGNORECASE | re.MULTILINE):
            if len(out) >= limit:
                break
            if isinstance(match, tuple):
                fact = " ".join(m.strip() for m in match if m.strip())
            else:
                fact = match.strip()
            if not fact or len(fact) < 6:
                continue
            clean = _strip_markdown_noise(fact)
            store = clean if clean else fact
            if not store or store in seen:
                continue
            if artifact_only:
                # Assistant channel only keeps hard artifacts.
                if re.match(r"^https?://", store, re.I):
                    pass
                elif _AUTO_EXTRACT_PATH_RE.match(store):
                    pass
                elif re.match(r"^v\d+\.\d+", store, re.I):
                    pass
                else:
                    continue
            elif not _auto_extract_fact_ok(store):
                continue
            seen.add(store)
            out.append(store)


def _word_count(text: str) -> int:
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    return len(parts)


def _looks_like_short_operational_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if _OPERATIONAL_QUERY_RE.match(q):
        return True
    if len(q) <= 12:
        return True
    return len(q) <= 24 and _word_count(q) <= 4


def should_auto_recall(
    query: str,
    *,
    has_working_state: bool,
    pipeline_run_id: str = "",
) -> bool:
    """
    Decide whether this turn should auto-inject long-term memories.

    Working state already carries the active task forward, so recall should be
    more selective and primarily activate for historical, preference, or
    decision-oriented prompts.
    """
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
        pipeline_run_id: str = "",
        workspace_dir_override: "Path | None" = None,
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

    - assemble(): stable prefix (role + static instructions + SOUL.md) +
                  dynamic suffix (date + score-gated memories + USER.md)
    - compact():  lossless — compress old turns to memory, replace with digest;
                  ``prune_tool_results`` strategy — zero LLM calls, replaces
                  tool message content with ``<pruned>``
    - after_turn(): lightweight regex-based fact extraction (no LLM calls);
                    optionally appends to USER.md if workspace is set.
                    Disable with auto_extract=False.
    """

    def __init__(
        self,
        auto_extract: bool = True,
        workspace_dir: "Path | None" = None,
    ) -> None:
        self.auto_extract = auto_extract
        self._workspace_dir = workspace_dir

    async def assemble(
        self,
        query: str,
        policy: ContextPolicy,
        memory: "MemoryStore",
        config: "AgentConfig",
        session_id: str | None = None,
        pipeline_run_id: str = "",
        workspace_dir_override: "Path | None" = None,
    ) -> tuple[str, str]:
        # --- Stable prefix (no date, no per-query content) ---
        base_prompt = config.system_prompt
        # Strip the {date} placeholder — it moves to dynamic suffix
        stable = base_prompt.replace(" Today is {date}.", "").replace("Today is {date}.", "")

        workspace_dir = workspace_dir_override if workspace_dir_override is not None else self._workspace_dir

        # Workspace AGENTS.md overrides config.agent.instructions (workspace-first).
        # Fallback to config.agent.instructions if AGENTS.md is absent.
        agents_injected = False
        if workspace_dir:
            agents_path = workspace_dir / "AGENTS.md"
            if agents_path.is_file():
                try:
                    agents_text = agents_path.read_text(encoding="utf-8").strip()
                    if agents_text:
                        stable += f"\n\n{SECTION_AGENT_INSTRUCTIONS}\n{agents_text}"
                        agents_injected = True
                except OSError as e:
                    log.warning("workspace file unreadable: %s — %s", agents_path, e)
        if not agents_injected and config.instructions:
            stable += f"\n\n{SECTION_INSTRUCTIONS}\n{config.instructions}"

        # Workspace SOUL.md → stable prefix (cacheable; rarely changes)
        if workspace_dir:
            soul_path = workspace_dir / "SOUL.md"
            if soul_path.is_file():
                try:
                    soul_text = soul_path.read_text(encoding="utf-8").strip()
                    if soul_text:
                        stable += f"\n\n{SECTION_WORKSPACE_IDENTITY}\n{soul_text}"
                except OSError as e:
                    log.warning("workspace file unreadable: %s — %s", soul_path, e)

        # --- Dynamic suffix (per-query fresh content) ---
        today = date.today().isoformat()
        dynamic_parts = [f"Today is {today}."]

        # Workspace USER.md → dynamic suffix (always fresh, per-query)
        if workspace_dir:
            user_path = workspace_dir / "USER.md"
            if user_path.is_file():
                try:
                    user_text = user_path.read_text(encoding="utf-8").strip()
                    if user_text:
                        dynamic_parts.append(f"{SECTION_USER_NOTES}\n{user_text}")
                except OSError as e:
                    log.warning("workspace file unreadable: %s — %s", user_path, e)

        if session_id:
            working_state = memory.load_session_working_state(session_id)
            if working_state:
                dynamic_parts.append(f"{SECTION_WORKING_STATE}\n{working_state}")
        else:
            working_state = None

        # Determine memory scopes: if agent has a memory_scope, restrict recall
        # to ["global", "agent:{scope}"] — else query all scopes (None = unfiltered).
        ms = config.memory_scope
        recall_scopes: list[str] | None = ["global", f"agent:{ms}"] if ms else None
        # Add workspace scope when a specific workspace is active
        if workspace_dir_override is not None:
            # Derive workspace name from directory basename for scoping
            ws_name = workspace_dir_override.name
            recall_scopes = (recall_scopes or ["global"]) + [f"workspace:{ws_name}"]
        # Add pipeline scope so each step can read artifacts from earlier steps
        if pipeline_run_id:
            recall_scopes = (recall_scopes or ["global"]) + [f"pipeline:{pipeline_run_id}"]

        # Score-gated, budget-capped memory injection (session-cached)
        serendipity = max(0.0, min(1.0, policy.serendipity_budget))
        if serendipity > 0.0:
            random_budget = int(policy.memory_max_tokens * serendipity)
            main_budget = policy.memory_max_tokens - random_budget
        else:
            main_budget = policy.memory_max_tokens
            random_budget = 0

        auto_recall = should_auto_recall(
            query,
            has_working_state=bool(working_state),
            pipeline_run_id=pipeline_run_id,
        )
        memories_text = ""
        if auto_recall:
            _t_recall = time.time()
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
            _recall_ms = (time.time() - _t_recall) * 1000
        else:
            _recall_ms = 0.0
        if memories_text:
            dynamic_parts.append(f"{SECTION_RECALLED_MEMORIES}\n{memories_text}")

        if random_budget > 0:
            _t_rand = time.time()
            random_memories = memory.recall_with_budget(
                "",
                min_score=0.1,
                max_tokens=random_budget,
                retrieval_temperature=1.0,
                scopes=recall_scopes,
                exclude_types={"action_log"},
            )
            _rand_ms = (time.time() - _t_rand) * 1000
            if random_memories:
                dynamic_parts.append(f"{SECTION_RANDOM_MEMORIES}\n{random_memories}")
        else:
            _rand_ms = 0.0

        log.info(
            "assemble: session=%s recall=%s %.0fms(%s) serendipity=%.0fms stable=%d dynamic=%d",
            (session_id or "?")[:12],
            "on" if auto_recall else "off",
            _recall_ms,
            "hit" if memories_text else "miss",
            _rand_ms,
            len(stable),
            len("\n\n".join(dynamic_parts)),
        )

        dynamic = "\n\n".join(dynamic_parts)
        return stable, dynamic

    @staticmethod
    def _compact_prune_tool_results(
        messages: list[Message],
        policy: ContextPolicy,
    ) -> list[Message]:
        """Replace tool-role message content with ``<pruned>`` for messages older
        than *compact_keep_turns* user-turns from the end.

        Zero LLM calls.  All ``user`` and ``assistant`` messages are kept intact.
        """
        keep = policy.compact_keep_turns
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= keep:
            return messages  # not enough turns to prune anything

        boundary = user_indices[-keep]  # keep the last N user-turns + everything after

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
        provider: "LLMProvider",
        model: str,
        memory: "MemoryStore",
        session_id: str,
    ) -> list[Message]:
        # Zero-LLM strategy: replace old tool messages with a placeholder
        if policy.compact_strategy == "prune_tool_results":
            return self._compact_prune_tool_results(messages, policy)

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
            summary_prompt = COMPACT_ABSTRACTIVE_TEMPLATE + "\n\nConversation to abstract:\n" + convo_text
        else:
            # "lossless" and "summarize" both use the detail-preserving structured prompt
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
                )
            compressed = [Message(role="user", content=f"{COMPACT_SUMMARY_PREFIX}\n{summary}")]
            working_state = memory.load_session_working_state(session_id)
            if working_state:
                compressed.append(Message(role="user", content=f"[Working state]\n{working_state}"))
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

        Zero LLM calls. Stores up to a few facts per turn, tagged _auto_extract.
        These have lower implicit relevance than user-saved memories.
        Disable via context.auto_extract = false in config.
        """
        if not self.auto_extract:
            return

        seen: set[str] = set()
        # typed_extractions: list of (fact_text, note_type) pairs, priority-ordered.
        typed_extractions: list[tuple[str, str]] = []

        def _collect_typed(text: str, patterns: list[str], note_type: str, *, artifact_only: bool = False) -> None:
            if not text or len(typed_extractions) >= _AUTO_EXTRACT_MAX_PER_TURN:
                return
            tmp: list[str] = []
            _extract_from_text(
                text, patterns,
                artifact_only=artifact_only,
                seen=seen,
                out=tmp,
                limit=_AUTO_EXTRACT_MAX_PER_TURN - len(typed_extractions),
            )
            for fact in tmp:
                # Drop action-log-like text — not durable user insights.
                if _ACTION_LOG_RE.search(fact):
                    log.debug("auto_extract: suppressed action_log fragment %r", fact[:40])
                    continue
                typed_extractions.append((fact, note_type))

        # Priority order: interest → belief → generic user facts → assistant artifacts
        _collect_typed(user_input, _INTEREST_PATTERNS, "interest")
        _collect_typed(user_input, _BELIEF_PATTERNS, "belief")
        _collect_typed(user_input, _AUTO_EXTRACT_USER_PATTERNS, "fact")
        _collect_typed(assistant_response, _AUTO_EXTRACT_ASSISTANT_PATTERNS, "fact", artifact_only=True)

        for fact, note_type in typed_extractions[:_AUTO_EXTRACT_MAX_PER_TURN]:
            try:
                note_title = f"Auto: {fact[:60]}"
                if memory.note_exists_with_title(note_title):
                    log.debug("auto_extract: skipping duplicate title %r", note_title[:40])
                    continue
                memory.remember(
                    fact,
                    title=note_title,
                    tags=["_auto_extract"],
                    note_type=note_type,
                    persist_to_disk=False,
                )
            except Exception as e:
                log.debug("auto_extract save failed: %s", e)

        # Correction signal detection: user negatively evaluated the last response.
        # Stored as a lightweight SQLite-only event tagged _correction for telemetry.
        # Deliberately kept separate from auto_extract quota to avoid crowding out facts.
        if user_input and _CORRECTION_RE.search(user_input):
            try:
                snippet = user_input[:120].replace("\n", " ")
                memory.remember(
                    f"correction in session {session_id[:8]}: {snippet}",
                    title=f"Correction: {snippet[:60]}",
                    tags=["_correction", session_id],
                    persist_to_disk=False,
                )
                log.debug("auto_extract: correction signal recorded for session %s", session_id[:8])
            except Exception as e:
                log.debug("correction signal save failed: %s", e)


def needs_compaction(messages: list[Message], policy: ContextPolicy) -> bool:
    """Return True if the message history exceeds the compaction threshold."""
    if policy.history_budget <= 0:
        return False
    current = estimate_messages_tokens(messages)
    threshold = int(policy.history_budget * policy.compact_threshold)
    return current >= threshold
