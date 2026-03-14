"""Agent: high-level class combining provider, memory, tools, and loop."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from ghostclaw.config import Config, load_config
from ghostclaw.context.engine import ContextEngine
from ghostclaw.loop import AgentLoop
from ghostclaw.memory.store import MemoryStore
from ghostclaw.providers.registry import get_provider
from ghostclaw.tools.registry import ToolRegistry
from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from ghostclaw.gateway import Gateway

log = get_logger("agent")


class Agent:
    """
    High-level Agent that wires together:
      - Config loading
      - LLM provider
      - Persistent memory store
      - Tool registry
      - AgentLoop (ReAct)
    """

    def __init__(
        self,
        config: Config | None = None,
        project_dir: Path | None = None,
        shared_memory: MemoryStore | None = None,
        context_engine: ContextEngine | None = None,
    ) -> None:
        self.config = config or load_config(project_dir)
        setup_logging(self.config.logging.level, self.config.logging.format)

        if shared_memory is not None:
            self.memory = shared_memory
        else:
            self.memory = MemoryStore(
                data_dir=self.config.memory.data_dir,
                embed_provider=self.config.memory.embed_provider,
                api_key=self.config.provider.api_key,
                fts_weight=self.config.memory.fts_weight,
                vec_weight=self.config.memory.vec_weight,
            )

        self.provider = get_provider(self.config.provider)
        self.context_engine = context_engine  # None → AgentLoop uses DefaultContextEngine

        self.registry = ToolRegistry()
        self.registry.load_builtins(
            enabled=self.config.tools.enabled,
            browser_enabled=self.config.browser.enabled,
        )
        if self.config.tools.plugin_dir:
            self.registry.load_plugins(self.config.tools.plugin_dir)

        skill_dir = self.config.tools.skill_dir
        if skill_dir and skill_dir.exists():
            from ghostclaw.skills.loader import SkillRegistry
            self._skill_registry = SkillRegistry(skill_dir)
            log.info("Loaded %d skills from %s", len(self._skill_registry), skill_dir)
        else:
            self._skill_registry = None

        self._scheduler = None  # set later by GhostClawServer after Scheduler is created

        log.info(
            "Agent ready: provider=%s model=%s tools=%d",
            self.config.provider.name,
            self.config.agent.model,
            len(self.registry),
        )

    def enable_agent_tools(self) -> None:
        """Register agent collaboration tools (call after gateway is available)."""
        from ghostclaw.tools.builtins import agent_tools
        self.registry.register_module(agent_tools)

    def new_loop(
        self,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
        context_engine: ContextEngine | None = None,
    ) -> AgentLoop:
        """Create a fresh AgentLoop for a new or resumed session."""
        return AgentLoop(
            config=self.config,
            provider=self.provider,
            memory=self.memory,
            registry=self.registry,
            session_id=session_id or make_id("s-"),
            gateway=gateway,
            context_engine=context_engine or self.context_engine,
            skill_registry=self._skill_registry,
            scheduler=self._scheduler,
        )

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """Single-shot chat (no session persistence across calls)."""
        loop = self.new_loop(session_id)
        return await loop.run(message)

    async def chat_stream(
        self, message: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Single-shot streaming chat."""
        loop = self.new_loop(session_id)
        async for chunk in loop.stream_run(message):
            yield chunk

    def remember(self, content: str, title: str = "", tags: list[str] | None = None) -> str:
        """Directly save a memory note, bypassing LLM."""
        return self.memory.remember(content, title=title, tags=tags)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Directly search memory, bypassing LLM."""
        return self.memory.search(query, limit=limit)

    def list_memories(self, limit: int = 20, tag: str | None = None) -> list[dict]:
        """List recent memory notes, optionally filtered by tag."""
        if tag:
            return self.memory.search_by_tag(tag, limit=limit)
        return self.memory.list_recent_notes(limit=limit)

    def forget(self, note_id: str) -> bool:
        """Delete a memory note by its ID. Returns True if deleted."""
        return self.memory.delete_note(note_id)

    def list_sessions(self) -> list[dict]:
        return self.memory.list_sessions()

    def close(self, close_memory: bool = True) -> None:
        if close_memory:
            self.memory.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *_) -> None:
        self.close()
