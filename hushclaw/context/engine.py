"""ContextEngine ABC and DefaultContextEngine implementation."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hushclaw.context.assembler import ContextAssembler, detect_response_mode, should_auto_recall
from hushclaw.context.compactor import CompactionService
from hushclaw.context.policy import ContextPolicy
from hushclaw.context.projector import TurnProjectionService
from hushclaw.memory.kinds import DECISION, PROJECT_KNOWLEDGE, TELEMETRY, USER_MODEL
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
    # Stable background facts or conventions
    r"(?:技术栈|约定|规范|环境|stack|convention|workflow)\s*[：:=]\s*(.{8,100}?)(?:[。\n]|$)",
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
    r"(?:为什么|怎么|如何|什么是|有没有|是否|能否|会不会)\s*(.{6,80}?)(?:[?？。\n]|$)",
    r"(?:why (?:does|is|would|did)|how (?:does|can|to|do)|what is|is there)\s+(.{8,100}?)(?:[?.\n]|$)",
]
_PREFERENCE_PATTERNS = [
    r"(?:用户偏好|偏好|习惯|我喜欢|我不喜欢|风格)\s*[：:]?\s*([^\n。！？!?]{4,80})(?:[。！？!?\n]|$)",
    r"(?:I prefer|I like|I don't like|my preference is|my usual workflow is)\s+(.{8,100}?)(?:[.\n]|$)",
]
_BELIEF_PATTERNS = [
    # Opinions and principles the user expresses
    r"(?:我认为|我觉得|我感觉|应该|不应该|这应该|这不应该)\s+(.{8,80}?)(?:[。\n]|$)",
    r"(?:I think|I believe|I feel|should(?:n't| not)|this should(?:n't| not))\s+(.{8,100}?)(?:[.\n]|$)",
]
_DECISION_PATTERNS = [
    r"(?:决定|结论|方案|选择了|最终|我们采用|确定使用)\s*[：:]?\s*([^\n。！？!?]{4,80})(?:[。！？!?\n]|$)",
    r"(?:decided to|we chose|the approach is|final decision|we settled on)\s*[：:\s]?\s*(.{8,100}?)(?:[.\n]|$)",
]
# Suppress auto-extraction of action-log-like text from user messages.
_ACTION_LOG_RE = re.compile(
    r"^(?:帮我|帮助我|已(?:修复|完成|创建|删除|更改|修改)|完成了|修改了|创建了|删除了|"
    r"updated|fixed|completed|created|deleted|I(?:'ve| have) (?:fixed|updated|created|deleted|completed))\s+.{5,}",
    re.IGNORECASE,
)
_REQUEST_LIKE_RE = re.compile(
    r"^(?:帮我|帮助我|请帮|我想要|我需要|目标|需求|任务|please |can you |could you |help me )",
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
    if len(t) < 6:
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
        calendar_timezone: str = "",
    ) -> None:
        self.auto_extract = auto_extract
        self._workspace_dir = workspace_dir
        self._calendar_timezone = calendar_timezone
        self._assembler = ContextAssembler(
            workspace_dir=workspace_dir,
            read_file_cached=self._read_file_cached,
            resolve_effective_timezone=self._resolve_effective_timezone,
            build_relative_day_anchors=self._build_relative_day_anchors,
        )
        self._compactor = CompactionService()
        # {str(path): (mtime, content)} — avoids re-reading unchanged workspace files
        self._file_cache: dict[str, tuple[float, str]] = {}
        self._turn_projector = TurnProjectionService(auto_extract=auto_extract)

    def _read_file_cached(self, path: Path) -> str | None:
        """Read a workspace file, returning a cached copy if the file is unchanged."""
        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._file_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            log.warning("workspace file unreadable: %s — %s", path, e)
            return None
        self._file_cache[key] = (mtime, content)
        return content

    def _resolve_effective_timezone(self):
        """Return the user's configured timezone, else the server's local timezone."""
        if self._calendar_timezone:
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo(self._calendar_timezone), self._calendar_timezone
            except Exception:
                pass
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        local_name = getattr(local_tz, "key", None) or str(local_tz)
        return local_tz, local_name

    @staticmethod
    def _build_relative_day_anchors(now_local: datetime) -> dict[str, str]:
        """Return local dates and UTC windows for yesterday/today/tomorrow."""
        anchors: dict[str, str] = {}
        for label, delta_days in (("yesterday", -1), ("today", 0), ("tomorrow", 1)):
            start_local = (now_local + timedelta(days=delta_days)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_local = start_local + timedelta(days=1)
            anchors[f"{label}_date"] = start_local.date().isoformat()
            anchors[f"{label}_from_utc"] = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            anchors[f"{label}_to_utc"] = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return anchors

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
        return await self._assembler.assemble(
            query,
            policy,
            memory,
            config,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
            workspace_dir_override=workspace_dir_override,
        )

    async def compact(
        self,
        messages: list[Message],
        policy: ContextPolicy,
        provider: "LLMProvider",
        model: str,
        memory: "MemoryStore",
        session_id: str,
    ) -> list[Message]:
        return await self._compactor.compact(
            messages,
            policy,
            provider,
            model,
            memory,
            session_id,
        )

    async def after_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        memory: "MemoryStore",
    ) -> None:
        """Delegate post-turn extraction to the lightweight turn projector."""
        await self._turn_projector.after_turn(
            session_id,
            user_input,
            assistant_response,
            memory,
        )


def needs_compaction(messages: list[Message], policy: ContextPolicy) -> bool:
    """Return True if the message history exceeds the compaction threshold."""
    if policy.history_budget <= 0:
        return False
    current = estimate_messages_tokens(messages)
    threshold = int(policy.history_budget * policy.compact_threshold)
    return current >= threshold
