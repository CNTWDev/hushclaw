"""Agent: high-level class combining provider, memory, tools, and loop."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from hushclaw.config import Config, load_config
from hushclaw.context.engine import ContextEngine
from hushclaw.loop import AgentLoop
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.registry import get_provider
from hushclaw.tools.registry import ToolRegistry
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from hushclaw.gateway import Gateway

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

        self._setup_registry(self.config)
        self._scheduler = None  # set later by HushClawServer after Scheduler is created

        log.info(
            "Agent ready: provider=%s model=%s tools=%d",
            self.config.provider.name,
            self.config.agent.model,
            len(self.registry),
        )

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def reload_runtime(self, new_config: Config) -> None:
        """Hot-reload provider/tools/skills from new config."""
        self.config = new_config
        self.provider = get_provider(new_config.provider)
        self._setup_registry(new_config)
        self.enable_agent_tools()

    def _setup_registry(self, config: Config) -> None:
        """Build ToolRegistry + SkillRegistry from config. Called by __init__ and reload_runtime."""
        from hushclaw.skills.loader import SkillRegistry
        from hushclaw.skills.loader import _BUILTINS_DIR as _SK_BUILTINS

        self.registry = ToolRegistry()
        self.registry.load_builtins(
            enabled=None,  # filter applied after all sources (builtins + plugins + skills)
            browser_enabled=config.browser.enabled,
        )
        if config.tools.plugin_dir:
            self.registry.load_plugins(config.tools.plugin_dir)

        # ── Three-tier SkillRegistry + bundled tool loading ──────────────────
        # Priority (ascending — later dirs override earlier):
        #   1. Built-ins (always loaded by SkillRegistry itself)
        #   2. system skill_dir
        #   3. user_skill_dir
        #   4. workspace .hushclaw/skills/ (highest priority)
        skill_dirs: list[Path] = []
        skill_dir = config.tools.skill_dir
        if skill_dir:
            skill_dirs.append(skill_dir)

        user_skill_dir = config.tools.user_skill_dir
        if user_skill_dir and user_skill_dir.exists():
            skill_dirs.append(user_skill_dir)

        # Workspace skills — auto-detected from workspace_dir
        workspace_skill_dir: Path | None = None
        if config.agent.workspace_dir:
            ws_skills = config.agent.workspace_dir / "skills"
            if ws_skills.is_dir():
                skill_dirs.append(ws_skills)
                workspace_skill_dir = ws_skills

        if skill_dirs or _SK_BUILTINS.exists():
            self._skill_registry = SkillRegistry(skill_dirs)
            log.info(
                "Loaded %d skills from %d source(s)",
                len(self._skill_registry), len(skill_dirs),
            )
        else:
            self._skill_registry = None

        # ── SkillManager — unified façade injected as _skill_manager ─────────
        from hushclaw.skills.installer import SkillInstaller
        from hushclaw.skills.validator import SkillValidator
        from hushclaw.skills.manager import SkillManager

        install_dir = config.tools.user_skill_dir or config.tools.skill_dir
        self._skill_manager = SkillManager(
            registry=self._skill_registry,
            installer=SkillInstaller(),
            validator=SkillValidator(),
            install_dir=install_dir,
            tool_registry=self.registry,
            # gateway bound later via set_gateway() in new_loop()
        )

        # Bundled tools — system skill tools (no namespace, may override builtins)
        if skill_dir and skill_dir.exists():
            for tools_dir in skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    self.registry.load_plugins(tools_dir)
                    log.info("Loaded bundled system tools from %s", tools_dir)

        # Bundled tools — user skill tools (namespaced to avoid collisions)
        if user_skill_dir and user_skill_dir.exists():
            for tools_dir in user_skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    skill_name = tools_dir.parent.name
                    self.registry.load_plugins(tools_dir, namespace=skill_name)
                    log.info("Loaded bundled user tools from %s", tools_dir)

        # Bundled tools — workspace skill tools (no namespace, highest priority)
        if workspace_skill_dir:
            for tools_dir in workspace_skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    self.registry.load_plugins(tools_dir)
                    log.info("Loaded bundled workspace tools from %s", tools_dir)

        # Apply profile preset (narrows tool universe) then the enabled filter.
        self.registry.apply_profile(config.tools.profile)
        self.registry.apply_enabled_filter(config.tools.enabled)

    def enable_agent_tools(self) -> None:
        """Register agent collaboration tools (call after gateway is available)."""
        from hushclaw.tools.builtins import agent_tools
        self.registry.register_module(agent_tools)

    def new_loop(
        self,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
        context_engine: ContextEngine | None = None,
    ) -> AgentLoop:
        """Create a fresh AgentLoop for a new or resumed session.

        If this ``session_id`` already has persisted turns (or a compaction summary)
        in ``MemoryStore``, the loop's message history is loaded so the model sees
        prior dialogue after process restart, session GC, or resuming from the UI.
        """
        if self._skill_manager is not None:
            self._skill_manager.set_gateway(gateway)
        loop = AgentLoop(
            config=self.config,
            provider=self.provider,
            memory=self.memory,
            registry=self.registry,
            session_id=session_id or make_id("s-"),
            gateway=gateway,
            context_engine=context_engine or self.context_engine,
            skill_registry=self._skill_registry,
            skill_manager=self._skill_manager,
            scheduler=self._scheduler,
        )
        loop.restore_session(loop.session_id)
        return loop

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
