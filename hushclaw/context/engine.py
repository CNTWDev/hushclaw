"""ContextEngine ABC and DefaultContextEngine implementation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hushclaw.context.assembler import ContextAssembler, detect_response_mode, should_auto_recall
from hushclaw.context.compactor import CompactionService
from hushclaw.context.policy import ContextPolicy
from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger
from hushclaw.util.tokens import estimate_messages_tokens

if TYPE_CHECKING:
    from hushclaw.config.schema import AgentConfig
    from hushclaw.memory.store import MemoryStore
    from hushclaw.prompt_blocks import PromptBlockRegistry
    from hushclaw.providers.base import LLMProvider

log = get_logger("context")

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
        references: list[dict] | None = None,
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
        source_message_id: str = "",
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
    - after_turn(): no-op; semantic memory extraction is handled by LearningController.
    """

    def __init__(
        self,
        auto_extract: bool = True,
        workspace_dir: "Path | None" = None,
        calendar_timezone: str = "",
        prompt_blocks: "PromptBlockRegistry | None" = None,
    ) -> None:
        self.auto_extract = auto_extract
        self._workspace_dir = workspace_dir
        self._calendar_timezone = calendar_timezone
        self._assembler = ContextAssembler(
            workspace_dir=workspace_dir,
            read_file_cached=self._read_file_cached,
            resolve_effective_timezone=self._resolve_effective_timezone,
            build_relative_day_anchors=self._build_relative_day_anchors,
            prompt_blocks=prompt_blocks,
        )
        self._compactor = CompactionService()
        # {str(path): (mtime, content)} — avoids re-reading unchanged workspace files
        self._file_cache: dict[str, tuple[float, str]] = {}

    def context_trace(self) -> dict:
        return self._assembler.context_trace()

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
        references: list[dict] | None = None,
    ) -> tuple[str, str]:
        return await self._assembler.assemble(
            query,
            policy,
            memory,
            config,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
            workspace_dir_override=workspace_dir_override,
            references=references or [],
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
        source_message_id: str = "",
    ) -> None:
        """No-op; semantic memory extraction is LLM-backed in LearningController."""
        return None


def needs_compaction(messages: list[Message], policy: ContextPolicy) -> bool:
    """Return True if the message history exceeds the compaction threshold."""
    if policy.history_budget <= 0:
        return False
    current = estimate_messages_tokens(messages)
    threshold = int(policy.history_budget * policy.compact_threshold)
    return current >= threshold
