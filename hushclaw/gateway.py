"""Gateway: AgentPool + Gateway for multi-agent routing and session affinity."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from hushclaw.config.schema import AgentDefinition, Config
from hushclaw.server.session import publish_session_event
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.agent import Agent
    from hushclaw.loop import AgentLoop
    from hushclaw.memory.store import MemoryStore

log = get_logger("gateway")
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CHILD_RUN_PROGRESS_POLL_SECONDS = 5.0
_CHILD_RUN_PROGRESS_LEASE_SECONDS = 90.0
_CHILD_RUN_STALL_TIMEOUT_SECONDS = 900.0


def _build_agent_from_definition(
    defn: AgentDefinition,
    config: Config,
    shared_memory: "MemoryStore | None" = None,
) -> "Agent":
    """Clone the global Config with per-agent overrides and build an Agent."""
    from hushclaw.agent import Agent

    agent_cfg = config.agent
    if defn.model:
        agent_cfg = dataclasses.replace(agent_cfg, model=defn.model)
    if defn.system_prompt:
        agent_cfg = dataclasses.replace(agent_cfg, system_prompt=defn.system_prompt)
    # Set memory scope to the agent's name so recall is layered: global + agent-scoped
    if defn.name and not agent_cfg.memory_scope:
        agent_cfg = dataclasses.replace(agent_cfg, memory_scope=defn.name)

    tools_cfg = config.tools
    if defn.tools:
        tools_cfg = dataclasses.replace(tools_cfg, enabled=defn.tools)

    new_config = dataclasses.replace(config, agent=agent_cfg, tools=tools_cfg)
    return Agent(config=new_config, shared_memory=shared_memory)


class AgentPool:
    """
    Manages concurrent access to a single named Agent.
    Maintains session-affinity: same session_id → same AgentLoop.
    Old sessions are garbage-collected based on session_ttl_hours.
    """

    def __init__(
        self,
        agent: "Agent",
        name: str,
        max_concurrent: int = 10,
        description: str = "",
        session_ttl_hours: int = 24,
    ) -> None:
        self._agent = agent
        self.name = name
        self._description = description
        if max_concurrent <= 0:
            log.warning(
                "AgentPool[%s]: max_concurrent=%d is invalid (must be ≥ 1), using 1",
                name, max_concurrent,
            )
            max_concurrent = 1
        self._sem = asyncio.Semaphore(max_concurrent)
        self._loops: dict[str, "AgentLoop"] = {}  # cache_key(session/thread) → loop
        self._loop_last_used: dict[str, float] = {}  # cache_key(session/thread) → unix timestamp
        self._session_ttl = session_ttl_hours * 3600

    def _drop_loop(self, loop: "AgentLoop") -> None:
        """Schedule sandbox cleanup for a single loop (shared by GC and explicit clear)."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(loop.aclose())
        else:
            running_loop.create_task(loop.aclose())

    def _gc_stale_sessions(self) -> None:
        """Remove AgentLoop entries that haven't been used within the TTL."""
        if self._session_ttl <= 0:
            return
        cutoff = time.time() - self._session_ttl
        stale = [sid for sid, ts in self._loop_last_used.items() if ts < cutoff]
        for sid in stale:
            loop = self._loops.pop(sid, None)
            self._loop_last_used.pop(sid, None)
            if loop is not None:
                self._drop_loop(loop)
            log.debug("GC'd stale session: %s", sid[:12])

    @staticmethod
    def _loop_cache_key(session_id: str | None = None, thread_id: str | None = None) -> str:
        if thread_id:
            return f"thread:{thread_id}"
        return f"session:{session_id or ''}"

    def _resolve_session_id(
        self,
        session_id: str | None,
        thread_id: str | None,
    ) -> str | None:
        if session_id:
            return session_id
        if not thread_id:
            return None
        row = self.memory.conn.execute(
            "SELECT session_id FROM threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Thread not found: {thread_id}")
        return str(row["session_id"])

    def _get_or_create_loop(
        self,
        session_id: str | None,
        thread_id: str | None,
        gateway: "Gateway | None",
    ) -> "AgentLoop":
        self._gc_stale_sessions()
        resolved_session_id = self._resolve_session_id(session_id, thread_id)
        cache_key = self._loop_cache_key(resolved_session_id, thread_id)
        if cache_key in self._loops:
            self._loop_last_used[cache_key] = time.time()
            return self._loops[cache_key]
        loop = self._agent.new_loop(resolved_session_id, thread_id=thread_id, gateway=gateway)
        rules = getattr(gateway, "_policy_rules", None) if gateway is not None else None
        if rules is not None:
            loop.tool_runtime.policy_gate.install_rules(
                can_call_tool=rules.can_call_tool,
                can_read_memory=rules.can_read_memory,
                can_use_connector=rules.can_use_connector,
            )
        self._loops[cache_key] = loop
        self._loop_last_used[cache_key] = time.time()
        return loop

    async def execute(
        self,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        gateway: "Gateway | None" = None,
        pipeline_run_id: str = "",
        images: list[str] | None = None,
    ) -> str:
        log.info("AgentPool[%s] waiting for semaphore (available=%d/%d) session=%s",
                 self.name, self._sem._value, self._sem._value + len(self._loops),
                 (session_id or "")[:12])
        async with self._sem:
            loop = self._get_or_create_loop(session_id, thread_id, gateway)
            loop.pipeline_run_id = pipeline_run_id
            log.info("AgentPool[%s] executing session=%s", self.name, loop.session_id[:12])
            try:
                result = await loop.run(text, images=images or [])
            finally:
                loop.pipeline_run_id = ""
            log.info("AgentPool[%s] done session=%s", self.name, loop.session_id[:12])
            return result

    async def stream(
        self,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        gateway: "Gateway | None" = None,
    ) -> AsyncIterator[str]:
        async with self._sem:
            loop = self._get_or_create_loop(session_id, thread_id, gateway)
            async for chunk in loop.stream_run(text):
                yield chunk

    async def event_stream(
        self,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        gateway: "Gateway | None" = None,
        pipeline_run_id: str = "",
        images: list[str] | None = None,
        workspace_dir=None,
        workspace_name: str = "",
        client_now: str = "",
        references: list[dict] | None = None,
        session_entry=None,
        parent_thread_id: str = "",
        parent_run_id: str = "",
        trigger_type: str = "",
        run_kind: str = "primary",
        visibility: str = "foreground",
    ) -> AsyncIterator[dict]:
        _t_wait = time.monotonic()
        async with self._sem:
            _sem_wait_ms = (time.monotonic() - _t_wait) * 1000
            if _sem_wait_ms > 5:
                log.info(
                    "AgentPool[%s] semaphore wait: %.0fms session=%s",
                    self.name, _sem_wait_ms, (session_id or "")[:12],
                )
            resolved_session_id = self._resolve_session_id(session_id, thread_id)
            effective_thread_id = thread_id
            if not effective_thread_id:
                if not resolved_session_id:
                    raise ValueError("session_id or thread_id is required")
                if parent_thread_id:
                    effective_thread_id = self.memory.create_child_thread(
                        resolved_session_id,
                        parent_thread_id,
                        agent_name=self.name,
                    )
                else:
                    effective_thread_id = self.memory.get_or_create_thread(
                        resolved_session_id,
                        agent_name=self.name,
                    )
            cache_key = self._loop_cache_key(resolved_session_id, effective_thread_id)
            loop = self._get_or_create_loop(resolved_session_id, effective_thread_id, gateway)
            setattr(loop, "_runtime_session_entry", session_entry)
            loop.pipeline_run_id = pipeline_run_id
            if client_now:
                loop.executor.set_context(_client_now=client_now)
            loop.executor.set_context(
                _current_thread_id=effective_thread_id,
                _current_parent_thread_id=str(parent_thread_id or ""),
                _current_run_id="",
                _current_parent_run_id=str(parent_run_id or ""),
                _current_run_kind=str(run_kind or "primary"),
                _current_run_visibility=str(visibility or "foreground"),
                _current_session_entry=session_entry,
            )

            # Phase 3: create/reuse thread and open a run for this execution.
            _sid = loop.session_id
            memory = loop.memory
            trigger = str(trigger_type or ("pipeline" if pipeline_run_id else "user"))
            run_id = memory.create_run(
                effective_thread_id,
                _sid,
                parent_run_id=str(parent_run_id or ""),
                trigger_type=trigger,
                run_kind=str(run_kind or "primary"),
                visibility=str(visibility or "foreground"),
            )
            loop.executor.set_context(_current_run_id=run_id)
            if session_entry is not None:
                bind_thread = getattr(session_entry, "bind_thread", None)
                if callable(bind_thread) and (run_kind or "primary") == "primary":
                    bind_thread(effective_thread_id, agent_name=self.name)
                begin_run = getattr(session_entry, "begin_run", None)
                if callable(begin_run) and (run_kind or "primary") == "primary":
                    begin_run(
                        {
                            "agent": self.name,
                            "text": text,
                            "images": list(images or []),
                            "workspace": workspace_name,
                            "client_now": client_now,
                            "references": list(references or []),
                        },
                        run_id=run_id,
                        trigger_type=trigger,
                    )
                register_child_run = getattr(session_entry, "register_child_run", None)
                if callable(register_child_run) and (run_kind or "primary") != "primary":
                    register_child_run(
                        run_id=run_id,
                        thread_id=effective_thread_id,
                        parent_run_id=str(parent_run_id or ""),
                        agent_name=self.name,
                        trigger_type=trigger,
                        run_kind=str(run_kind or "child"),
                        visibility=str(visibility or "background"),
                        state="running",
                        summary=f"Running {self.name}",
                    )
            memory.session_log.append(
                _sid,
                "run_started",
                {
                    "agent": self.name,
                    "trigger": trigger,
                    "run_kind": str(run_kind or "primary"),
                    "visibility": str(visibility or "foreground"),
                    "parent_run_id": str(parent_run_id or ""),
                },
                thread_id=effective_thread_id, run_id=run_id,
            )

            try:
                yield {
                    "type": "thread_run_bound",
                    "thread_id": effective_thread_id,
                    "run_id": run_id,
                    "trigger_type": trigger,
                    "agent": self.name,
                    "parent_thread_id": str(parent_thread_id or ""),
                    "parent_run_id": str(parent_run_id or ""),
                    "run_kind": str(run_kind or "primary"),
                    "visibility": str(visibility or "foreground"),
                }
                async for event in loop.event_stream(
                    text, images=images or [], workspace_dir=workspace_dir, workspace_name=workspace_name,
                    thread_id=effective_thread_id, run_id=run_id,
                    references=references or [],
                ):
                    yield event
                memory.complete_run(run_id)
                memory.session_log.append(
                    _sid, "run_completed", {},
                    thread_id=effective_thread_id, run_id=run_id,
                )
            except Exception:
                memory.fail_run(run_id)
                memory.session_log.append(
                    _sid, "run_failed", {},
                    thread_id=effective_thread_id, run_id=run_id,
                )
                raise
            finally:
                loop.pipeline_run_id = ""
                setattr(loop, "_runtime_session_entry", None)
                # Close sandbox for ephemeral loops (not in the pool).
                # Pooled loops are closed by _gc_stale_sessions() on TTL expiry.
                if cache_key not in self._loops:
                    self._drop_loop(loop)

    @property
    def memory(self) -> "MemoryStore":
        return self._agent.memory

    def set_scheduler(self, scheduler) -> None:
        self._agent.set_scheduler(scheduler)

    def clear_cached_loops(self) -> None:
        loops = list(self._loops.values())
        self._loops.clear()
        self._loop_last_used.clear()
        for loop in loops:
            self._drop_loop(loop)


class Gateway:
    """
    Central router: maintains a pool per named agent, routes requests by name,
    supports broadcast, and provides session affinity.
    """

    def __init__(self, config: Config, base_agent: "Agent") -> None:
        self._config = config
        self._base_agent = base_agent
        self._session_title_update_callback = None
        self._pools: dict[str, AgentPool] = {}
        self._agent_descriptions: dict[str, str] = {}
        self._agent_meta: dict[str, dict] = {}
        self._runtime_defs: list[dict] = []  # persisted dynamic agent definitions
        self._policy_rules: Any = None       # set by DistroRuntime.install_policy_rules()
        self.handover_registry: dict[str, asyncio.Event] = {}  # session_id → Event for browser handover
        self._build_pools(base_agent)
        self._load_dynamic_agents()

    @property
    def base_agent(self) -> "Agent":
        return self._base_agent

    @property
    def config(self) -> Config:
        return self._config

    @property
    def memory(self) -> "MemoryStore":
        return self._base_agent.memory

    def pools(self) -> tuple[AgentPool, ...]:
        return tuple(self._pools.values())

    def set_scheduler(self, scheduler) -> None:
        self._base_agent.set_scheduler(scheduler)
        for pool in self._pools.values():
            pool.set_scheduler(scheduler)

    def set_session_title_update_callback(self, callback) -> None:
        self._session_title_update_callback = callback
        self._base_agent.set_session_title_update_callback(callback)
        for pool in self._pools.values():
            pool._agent.set_session_title_update_callback(callback)

    def clear_all_cached_loops(self) -> None:
        for pool in self._pools.values():
            pool.clear_cached_loops()

    def install_policy_rules(self, rules: Any) -> None:
        """Store distro PolicyRuleSet for injection into each new AgentLoop's PolicyGate.

        Called by DistroRuntime.assemble() after gateway construction.
        Rules are applied when AgentPool creates a new loop for a session.
        """
        self._policy_rules = rules

    @staticmethod
    def _implicit_session_id(agent_name: str) -> str:
        # Keep non-interactive calls from exploding session cardinality.
        return f"auto_{agent_name}"

    def _resolve_workspace(self, workspace: str | None) -> "Path | None":
        """Resolve workspace name → Path from the registry. Returns None if not found."""
        if not workspace:
            return None
        from pathlib import Path as _Path
        for ws in self._config.workspaces.list:
            if ws.name == workspace:
                return _Path(ws.path)
        return None

    @staticmethod
    def _child_run_lease_expires_at_ms() -> int:
        return int((time.time() + _CHILD_RUN_PROGRESS_LEASE_SECONDS) * 1000)

    async def move_session_workspace(self, session_id: str, workspace: str) -> None:
        """Reassign session (and all its turns) to a different workspace, then evict loop cache."""
        if workspace and not self._resolve_workspace(workspace):
            raise ValueError(f"Unknown workspace: {workspace!r}")
        # Evict cached loop so next query re-loads agent context from the new workspace
        for pool in self._pools.values():
            pool.clear_cached_loops()
            break  # only need to clear for the session's owning pool; clear_cached_loops is safe to call broadly
        # Persist workspace change — use the base agent's shared memory store
        memory = self._base_agent.memory
        if memory is not None:
            memory.move_session_workspace(session_id, workspace)

    def _build_pools(self, base_agent: "Agent") -> None:
        max_c = self._config.gateway.max_concurrent_per_agent
        ttl = self._config.gateway.session_ttl_hours

        # Default pool uses the provided base agent
        self._pools["default"] = AgentPool(base_agent, "default", max_c, "Default agent", ttl)
        self._agent_descriptions["default"] = "Default agent"
        self._agent_meta["default"] = {
            "routing_tags": [],
            "domain_id": "",
            "owner_type": "runtime",
            "visibility": "",
        }

        # Enable agent tools on the base agent (gateway is now available)
        base_agent.set_session_title_update_callback(self._session_title_update_callback)
        base_agent.enable_agent_tools()

        shared_memory = base_agent.memory if self._config.gateway.shared_memory else None

        # First pass: build all pools and register meta.
        for defn in self._config.gateway.agents:
            agent = _build_agent_from_definition(defn, self._config, shared_memory)
            agent.set_session_title_update_callback(self._session_title_update_callback)
            agent.enable_agent_tools()
            pool = AgentPool(agent, defn.name, max_c, defn.description, ttl)
            self._pools[defn.name] = pool
            self._agent_descriptions[defn.name] = defn.description
            self._agent_meta[defn.name] = {
                "routing_tags": self._normalize_tags(defn.routing_tags),
                "domain_id": "",
                "owner_type": "config",
                "visibility": "",
            }
            log.info(
                "Registered agent pool: name=%s model=%s tools=%d",
                defn.name,
                agent.config.agent.model,
                len(agent.registry),
            )

    @staticmethod
    def _normalize_tags(tags: list[str] | str | None) -> list[str]:
        if not tags:
            return []
        # Guard: LLM may pass a comma-separated string when the JSON schema was
        # incorrectly inferred as "string" instead of "array".  Split on commas
        # so we don't iterate the string character-by-character.
        if isinstance(tags, str):
            tags = [s for s in (s.strip() for s in tags.split(",")) if s]
        out: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            val = (tag or "").strip()
            if val and val not in seen:
                seen.add(val)
                out.append(val)
        return out

    def _validate_agent_name(self, name: str) -> str:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Agent name is required.")
        if clean == "default":
            raise ValueError("'default' is a reserved agent name.")
        if not _AGENT_NAME_RE.fullmatch(clean):
            raise ValueError(
                "Invalid agent name. Use only letters, numbers, '.', '_' or '-'."
            )
        return clean

    def _legacy_dynamic_agents_path(self):
        if self._config.memory.data_dir is None:
            return None
        return self._config.memory.data_dir / "dynamic_agents.json"

    def _dynamic_agents_path(self):
        workspace_dir = self._config.agent.workspace_dir
        if workspace_dir is not None:
            return Path(workspace_dir).expanduser() / "dynamic_agents.json"
        return self._legacy_dynamic_agents_path()

    def _load_dynamic_agents(self) -> None:
        path = self._dynamic_agents_path()
        legacy_path = self._legacy_dynamic_agents_path()
        load_path = path
        if load_path is None:
            return
        if not load_path.exists() and legacy_path is not None and legacy_path != load_path and legacy_path.exists():
            log.info(
                "Loading legacy dynamic agents from %s (workspace file not present yet)",
                legacy_path,
            )
            load_path = legacy_path
        if not load_path.exists():
            return
        try:
            defs = json.loads(load_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Could not read dynamic_agents.json from %s: %s", load_path, e)
            return
        for d in defs:
            name = d.get("name", "")
            if name in self._pools:
                log.debug("Dynamic agent '%s' already defined in config, skipping", name)
                continue
            try:
                self._register_agent(
                    name=name,
                    description=d.get("description", ""),
                    system_prompt=d.get("system_prompt", ""),
                    instructions=d.get("instructions", ""),
                    routing_tags=d.get("routing_tags", []),
                    tools=d.get("tools", []),
                )
                self._runtime_defs.append({
                    "name": name,
                    "description": d.get("description", ""),
                    "system_prompt": d.get("system_prompt", ""),
                    "instructions": d.get("instructions", ""),
                    "routing_tags": self._normalize_tags(d.get("routing_tags", [])),
                    "tools": d.get("tools", []),
                })
                log.info("Restored dynamic agent: name=%s", name)
            except Exception as e:
                log.warning("Skipping dynamic agent '%s': %s", name, e)

    def _save_dynamic_agents(self) -> None:
        path = self._dynamic_agents_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._runtime_defs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("Could not save dynamic_agents.json: %s", e)

    def _register_agent(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        instructions: str = "",
        routing_tags: list[str] | None = None,
        tools: list[str] | None = None,
        domain_id: str = "",
        owner_type: str = "runtime",
        visibility: str = "",
    ) -> None:
        """Internal: build and register an agent pool (no persistence side-effects)."""
        defn = AgentDefinition(
            name=name,
            description=description,
            model="",
            system_prompt=system_prompt,
            tools=tools or [],
        )
        shared_memory = self._base_agent.memory if self._config.gateway.shared_memory else None
        # Use base_agent's current config (always up-to-date after wizard changes)
        # so new dynamic agents inherit the active provider, not the stale startup config.
        agent = _build_agent_from_definition(defn, self._base_agent.config, shared_memory)
        if instructions:
            agent.config = dataclasses.replace(
                agent.config,
                agent=dataclasses.replace(agent.config.agent, instructions=instructions),
            )
        agent.set_session_title_update_callback(self._session_title_update_callback)
        agent.enable_agent_tools()
        ttl = self._config.gateway.session_ttl_hours
        max_c = self._config.gateway.max_concurrent_per_agent
        pool = AgentPool(agent, name, max_c, description, ttl)
        scheduler = getattr(self._base_agent, "_scheduler", None)
        if scheduler is not None:
            pool.set_scheduler(scheduler)
        self._pools[name] = pool
        self._agent_descriptions[name] = description
        self._agent_meta[name] = {
            "routing_tags": self._normalize_tags(routing_tags),
            "domain_id": domain_id or "",
            "owner_type": owner_type or "runtime",
            "visibility": visibility or "",
        }

    def create_agent(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        instructions: str = "",
        routing_tags: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        """Register a new agent pool at runtime and persist it across restarts."""
        name = self._validate_agent_name(name)
        if name in self._pools:
            raise ValueError(f"Agent '{name}' already exists.")

        self._register_agent(
            name=name,
            description=description,
            system_prompt=system_prompt,
            instructions=instructions,
            routing_tags=routing_tags,
            tools=tools,
        )
        self._runtime_defs.append({
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "instructions": instructions,
            "routing_tags": self._normalize_tags(routing_tags),
            "tools": tools or [],
        })
        self._save_dynamic_agents()
        # Agent topology changes invalidate cached conversational assumptions
        # such as prior list_agents results.
        self.clear_all_cached_loops()
        log.info("Registered runtime agent: name=%s", name)

    def register_domain_agent(self, definition: dict) -> None:
        """Register a non-editable domain-owned agent at runtime."""
        name = self._validate_agent_name(str(definition.get("name") or ""))
        if not name:
            raise ValueError("Domain agent name cannot be empty.")
        existing = self._agent_meta.get(name) or {}
        if name in self._pools:
            if existing.get("owner_type") == "domain":
                return
            raise ValueError(f"Agent '{name}' already exists.")
        self._register_agent(
            name=name,
            description=str(definition.get("description") or ""),
            system_prompt=str(definition.get("system_prompt") or ""),
            instructions=str(definition.get("instructions") or ""),
            routing_tags=definition.get("routing_tags") or [],
            tools=definition.get("tools") or [],
            domain_id=str(definition.get("domain_id") or ""),
            owner_type="domain",
            visibility=str(definition.get("visibility") or "employee_visible"),
        )
        self.clear_all_cached_loops()
        log.info("Registered domain agent: name=%s domain=%s", name, definition.get("domain_id") or "")

    def unregister_domain_agents(self, domain_id: str) -> None:
        """Remove all non-editable agents owned by a disabled domain."""
        names = [
            name for name, meta in self._agent_meta.items()
            if meta.get("owner_type") == "domain" and meta.get("domain_id") == domain_id
        ]
        for name in names:
            pool = self._pools.pop(name, None)
            if pool is not None:
                for loop in list(pool._loops.values()):
                    pool._drop_loop(loop)
            self._agent_descriptions.pop(name, None)
            self._agent_meta.pop(name, None)
        if names:
            self.clear_all_cached_loops()
            log.info("Unregistered domain agents: domain=%s agents=%s", domain_id, names)

    def delete_agent(self, name: str) -> None:
        """Remove a runtime-created agent. Cannot delete 'default' or config-defined agents."""
        if name == "default":
            raise ValueError("Cannot delete the default agent.")
        if name not in self._pools:
            raise ValueError(f"Agent '{name}' not found.")
        del self._pools[name]
        del self._agent_descriptions[name]
        self._agent_meta.pop(name, None)
        self._runtime_defs = [d for d in self._runtime_defs if d["name"] != name]
        self._save_dynamic_agents()
        # Clear all cached loops so no live session keeps stale agent topology
        # or previously cached list_agents/tool results in memory.
        self.clear_all_cached_loops()
        log.info("Deleted runtime agent: name=%s", name)

    def get_agent_def(self, name: str) -> dict | None:
        """Return the full configuration dict for a named agent, or None if not found."""
        if name not in self._pools:
            return None
        runtime_names = {d["name"] for d in self._runtime_defs}
        d = next((d for d in self._runtime_defs if d["name"] == name), None)
        if d:
            return {
                **d,
                "routing_tags": self._normalize_tags(d.get("routing_tags", [])),
                "editable": True,
            }
        # config-defined agent: reconstruct from pool's agent config
        pool = self._pools[name]
        cfg = pool._agent.config
        meta = self._agent_meta.get(name, {})
        return {
            "name": name,
            "description": self._agent_descriptions.get(name, ""),
            "model": cfg.agent.model,
            "system_prompt": cfg.agent.system_prompt,
            "instructions": cfg.agent.instructions,
            "routing_tags": self._normalize_tags(meta.get("routing_tags", [])),
            "tools": list(cfg.tools.enabled) if cfg.tools.enabled else [],
            "editable": False,
        }

    def update_agent(
        self,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        instructions: str | None = None,
        routing_tags: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        """Update a runtime agent's fields. Config-defined agents cannot be updated at runtime."""
        if name == "default":
            raise ValueError("Cannot update the default agent.")
        if name not in self._pools:
            raise ValueError(f"Agent '{name}' not found.")
        d = next((d for d in self._runtime_defs if d["name"] == name), None)
        if d is None:
            raise ValueError(f"Agent '{name}' is config-defined and cannot be updated at runtime.")
        d.pop("model", None)
        if description is not None:
            d["description"] = description
        if system_prompt is not None:
            d["system_prompt"] = system_prompt
        if instructions is not None:
            d["instructions"] = instructions
        if routing_tags is not None:
            d["routing_tags"] = self._normalize_tags(routing_tags)
        if tools is not None:
            d["tools"] = tools
        # Tear down existing pool and re-register with updated definition
        del self._pools[name]
        del self._agent_descriptions[name]
        self._agent_meta.pop(name, None)
        self._register_agent(
            name=name,
            description=d.get("description", ""),
            system_prompt=d.get("system_prompt", ""),
            instructions=d.get("instructions", ""),
            routing_tags=d.get("routing_tags", []),
            tools=d.get("tools", []),
        )
        self._save_dynamic_agents()
        # Updating agent metadata changes list_agents output.
        self.clear_all_cached_loops()
        log.info("Updated runtime agent: name=%s", name)

    def get_pool(self, name: str) -> AgentPool:
        return self._pools.get(name) or self._pools["default"]

    def resolve_pipeline(self, name_or_agents: str | list[str]) -> list[str]:
        """
        Resolve a pipeline spec: a named pipeline from config or a list of agent names.
        Named pipelines are defined under [gateway.pipelines] in the config file.
        """
        if isinstance(name_or_agents, list):
            return name_or_agents
        # Check if it's a named pipeline in config
        named = self._config.gateway.pipelines.get(name_or_agents)
        if named is not None:
            return named
        # Otherwise treat as comma-separated agent list
        return [n.strip() for n in name_or_agents.split(",") if n.strip()]

    def list_agents(self) -> list[dict]:
        runtime_names = {d["name"] for d in self._runtime_defs}
        return [
            {
                "name": n,
                "description": self._agent_descriptions.get(n, ""),
                "routing_tags": self._normalize_tags(self._agent_meta.get(n, {}).get("routing_tags", [])),
                "domain_id": self._agent_meta.get(n, {}).get("domain_id", "") or "",
                "owner_type": self._agent_meta.get(n, {}).get("owner_type", "runtime") or "runtime",
                "visibility": self._agent_meta.get(n, {}).get("visibility", "") or "",
                "editable": n in runtime_names,
            }
            for n in self._pools
        ]

    async def execute(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        pipeline_run_id: str = "",
        images: list[str] | None = None,
        parent_thread_id: str = "",
        parent_run_id: str = "",
        trigger_type: str = "",
        run_kind: str = "primary",
        visibility: str = "foreground",
        workspace: str | None = None,
        client_now: str = "",
        references: list[dict] | None = None,
        session_entry=None,
    ) -> str:
        if session_id is None and thread_id is None:
            session_id = self._implicit_session_id(agent_name)
        log.info("Gateway.execute: agent=%s session=%s input=%r",
                 agent_name, (session_id or "")[:12], text[:80])
        result = ""
        bound_run_id = ""
        child_like = (run_kind or "primary") != "primary" or bool(parent_run_id)
        set_child_run_state = getattr(session_entry, "set_child_run_state", None) if session_entry is not None else None
        touch_child_run = getattr(session_entry, "touch_child_run", None) if session_entry is not None else None
        complete_child_run = getattr(session_entry, "complete_child_run", None) if session_entry is not None else None
        progress_queue: asyncio.Queue[dict | object] = asyncio.Queue()
        producer_done = asyncio.Event()
        stream_sentinel = object()
        producer_error: BaseException | None = None
        producer_task: asyncio.Task | None = None
        last_progress_at = time.monotonic()
        last_progress_kind = "queued"
        child_marked_stale = False

        async def _emit_child_runtime(state: str, summary: str = "", *, step_id: str = "", step_type: str = "", step_state: str = "", meta: dict | None = None) -> None:
            if not child_like or session_entry is None or not bound_run_id:
                return
            runtime_meta = session_entry.runtime_meta() if hasattr(session_entry, "runtime_meta") else {}
            await publish_session_event(
                session_entry,
                {
                    "type": "child_run_state_changed",
                    "run_id": bound_run_id,
                    "thread_id": str(runtime_meta.get("thread_id") or ""),
                    "parent_run_id": str(parent_run_id or ""),
                    "state": state,
                    "agent": agent_name,
                    "run_kind": str(run_kind or "child"),
                    "visibility": str(visibility or "background"),
                    "summary": summary,
                    "step_id": step_id,
                    "step_type": step_type,
                    "step_state": step_state,
                    "meta": dict(meta or {}),
                },
            )
            await publish_session_event(
                session_entry,
                {
                    "type": "session_runtime",
                    "runtime": {
                        "session_id": session_entry.session_id,
                        "status": "running" if session_entry.is_running() else "idle",
                        "phase": "thinking",
                        "summary": runtime_meta.get("active_step", {}).get("summary") or "Running",
                        "agent": runtime_meta.get("thread_agent") or agent_name,
                        "thread_id": runtime_meta.get("thread_id") or "",
                        "thread_state": runtime_meta.get("thread_state") or "",
                        "thread_agent": runtime_meta.get("thread_agent") or "",
                        "run_id": runtime_meta.get("run_id") or "",
                        "run_seq": runtime_meta.get("run_seq") or 0,
                        "run_state": runtime_meta.get("run_state") or "",
                        "trigger_type": runtime_meta.get("trigger_type") or "user",
                        "pending_amendments": runtime_meta.get("pending_amendments", 0),
                        "last_completed_run_id": runtime_meta.get("last_completed_run_id") or "",
                        "last_superseded_run_id": runtime_meta.get("last_superseded_run_id") or "",
                        "last_amendment_id": runtime_meta.get("last_amendment_id") or "",
                        "active_step": runtime_meta.get("active_step") or {},
                        "child_runs": list(runtime_meta.get("child_runs") or []),
                        "updated_at": int(time.time() * 1000),
                    },
                },
            )

        def _touch_progress(event_type: str) -> None:
            nonlocal last_progress_at, last_progress_kind, child_marked_stale
            last_progress_at = time.monotonic()
            last_progress_kind = event_type or "event"
            if child_like and callable(touch_child_run) and bound_run_id:
                touch_child_run(
                    bound_run_id,
                    progress_kind=last_progress_kind,
                    lease_expires_at=self._child_run_lease_expires_at_ms(),
                    stale=False if child_marked_stale else None,
                )
            child_marked_stale = False

        async def _produce_events() -> None:
            nonlocal producer_error
            try:
                async for event in self.event_stream(
                    agent_name,
                    text,
                    session_id=session_id,
                    thread_id=thread_id,
                    images=images or [],
                    workspace=workspace,
                    client_now=client_now,
                    references=references or [],
                    session_entry=session_entry,
                    pipeline_run_id=pipeline_run_id,
                    parent_thread_id=parent_thread_id,
                    parent_run_id=parent_run_id,
                    trigger_type=trigger_type,
                    run_kind=run_kind,
                    visibility=visibility,
                ):
                    await progress_queue.put(event)
            except BaseException as exc:
                producer_error = exc
            finally:
                producer_done.set()
                await progress_queue.put(stream_sentinel)
        try:
            producer_task = asyncio.create_task(_produce_events())
            while True:
                try:
                    item = await asyncio.wait_for(
                        progress_queue.get(),
                        timeout=_CHILD_RUN_PROGRESS_POLL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if producer_done.is_set():
                        continue
                    if child_like and callable(set_child_run_state) and bound_run_id:
                        idle_for = time.monotonic() - last_progress_at
                        if idle_for >= _CHILD_RUN_STALL_TIMEOUT_SECONDS:
                            raise RuntimeError(
                                f"Sub-agent stalled after {idle_for:.0f}s without progress"
                            )
                        if idle_for >= _CHILD_RUN_PROGRESS_LEASE_SECONDS and not child_marked_stale:
                            child_marked_stale = True
                            summary = f"No progress for {int(idle_for)}s"
                            set_child_run_state(
                                bound_run_id,
                                state="stale",
                                summary=summary,
                                step_id=f"stale:{bound_run_id}",
                                step_type="watchdog",
                                step_state="stalled",
                                meta={"idle_seconds": int(idle_for), "last_progress_kind": last_progress_kind},
                            )
                            await _emit_child_runtime(
                                "stale",
                                summary,
                                step_id=f"stale:{bound_run_id}",
                                step_type="watchdog",
                                step_state="stalled",
                                meta={"idle_seconds": int(idle_for), "last_progress_kind": last_progress_kind},
                            )
                    continue

                if item is stream_sentinel:
                    break

                event = item
                event_type = str(event.get("type") or "")
                was_stale = child_marked_stale
                _touch_progress(event_type)
                if event_type == "thread_run_bound":
                    bound_run_id = str(event.get("run_id") or "")
                    if child_like and callable(touch_child_run) and bound_run_id:
                        touch_child_run(
                            bound_run_id,
                            progress_kind="thread_run_bound",
                            lease_expires_at=self._child_run_lease_expires_at_ms(),
                        )
                    await _emit_child_runtime("running", f"Running {agent_name}")
                if was_stale and event_type not in {"thread_run_bound", "done"}:
                    if callable(set_child_run_state) and bound_run_id:
                        set_child_run_state(bound_run_id, state="running", summary=f"Running {agent_name}")
                    await _emit_child_runtime("running", f"Running {agent_name}")
                if child_like and callable(set_child_run_state) and bound_run_id:
                    if event_type == "round_info":
                        round_no = int(event.get("round") or 0)
                        max_rounds = int(event.get("max_rounds") or 0)
                        summary = "Thinking"
                        if round_no and max_rounds:
                            summary = f"Thinking · round {round_no}/{max_rounds}"
                        set_child_run_state(
                            bound_run_id,
                            state="running",
                            summary=summary,
                            step_id=f"model:{bound_run_id}:{round_no}",
                            step_type="model",
                            step_state="running",
                            meta={"round": round_no, "max_rounds": max_rounds},
                        )
                        await _emit_child_runtime(
                            "running",
                            summary,
                            step_id=f"model:{bound_run_id}:{round_no}",
                            step_type="model",
                            step_state="running",
                            meta={"round": round_no, "max_rounds": max_rounds},
                        )
                    elif event_type == "tool_call":
                        tool = str(event.get("tool") or "tool")
                        call_id = str(event.get("call_id") or f"tool:{bound_run_id}")
                        set_child_run_state(
                            bound_run_id,
                            state="running",
                            summary=f"Using {tool}",
                            step_id=call_id,
                            step_type="tool",
                            step_state="running",
                            meta={"tool": tool},
                        )
                        await _emit_child_runtime(
                            "running",
                            f"Using {tool}",
                            step_id=call_id,
                            step_type="tool",
                            step_state="running",
                            meta={"tool": tool},
                        )
                    elif event_type == "awaiting_user":
                        set_child_run_state(
                            bound_run_id,
                            state="paused",
                            summary="Waiting for you",
                            step_id=f"approval:{bound_run_id}",
                            step_type="approval",
                            step_state="waiting",
                            meta={"pending_tools": list(event.get("pending_tools") or [])},
                        )
                        await _emit_child_runtime(
                            "paused",
                            "Waiting for you",
                            step_id=f"approval:{bound_run_id}",
                            step_type="approval",
                            step_state="waiting",
                            meta={"pending_tools": list(event.get("pending_tools") or [])},
                        )
                    elif event_type == "user_amendment_applied":
                        set_child_run_state(
                            bound_run_id,
                            state="running",
                            summary="Applying latest update",
                            step_id=str(event.get("amendment_id") or f"amendment:{bound_run_id}"),
                            step_type="amendment",
                            step_state="applied",
                            meta={"safe_point": str(event.get("safe_point") or "")},
                        )
                        await _emit_child_runtime(
                            "running",
                            "Applying latest update",
                            step_id=str(event.get("amendment_id") or f"amendment:{bound_run_id}"),
                            step_type="amendment",
                            step_state="applied",
                            meta={"safe_point": str(event.get("safe_point") or "")},
                        )
                if event_type == "done":
                    result = str(event.get("text") or "")
                    if child_like and callable(complete_child_run) and bound_run_id:
                        stop_reason = str(event.get("stop_reason") or "")
                        final_state = "superseded" if stop_reason == "user_amendment" else "completed"
                        complete_child_run(bound_run_id, state=final_state, summary="Done" if final_state == "completed" else "Superseded")
                        await _emit_child_runtime(final_state, "Done" if final_state == "completed" else "Superseded")
            await producer_task
            if producer_error is not None:
                raise producer_error
        except Exception:
            if producer_task is not None and not producer_task.done():
                producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer_task
            if child_like and callable(complete_child_run) and bound_run_id:
                complete_child_run(bound_run_id, state="failed", summary="Failed")
                await _emit_child_runtime("failed", "Failed")
            raise
        log.info("Gateway.execute done: agent=%s result=%r", agent_name, (result or "")[:80])
        return result

    async def stream(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
    ) -> AsyncIterator[str]:
        if session_id is None and thread_id is None:
            session_id = self._implicit_session_id(agent_name)
        pool = self.get_pool(agent_name)
        async for chunk in pool.stream(text, session_id, thread_id=thread_id, gateway=self):
            yield chunk

    async def event_stream(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        images: list[str] | None = None,
        workspace: str | None = None,
        client_now: str = "",
        references: list[dict] | None = None,
        session_entry=None,
        pipeline_run_id: str = "",
        parent_thread_id: str = "",
        parent_run_id: str = "",
        trigger_type: str = "",
        run_kind: str = "primary",
        visibility: str = "foreground",
    ) -> AsyncIterator[dict]:
        if session_id is None and thread_id is None:
            session_id = self._implicit_session_id(agent_name)
        pool = self.get_pool(agent_name)
        workspace_dir = self._resolve_workspace(workspace)
        log.info(
            "Gateway.event_stream: agent=%s session=%s workspace=%r workspace_dir=%r client_now=%s input=%r",
            agent_name,
            (session_id or "")[:12],
            workspace,
            str(workspace_dir) if workspace_dir is not None else None,
            client_now or "(none)",
            text[:120],
        )
        async for event in pool.event_stream(
            text,
            session_id,
            thread_id=thread_id,
            gateway=self,
            pipeline_run_id=pipeline_run_id,
            images=images or [],
            workspace_dir=workspace_dir,
            workspace_name=workspace or "",
            client_now=client_now,
            references=references or [],
            session_entry=session_entry,
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            trigger_type=trigger_type,
            run_kind=run_kind,
            visibility=visibility,
        ):
            yield event

    async def broadcast(
        self,
        agent_names: list[str],
        text: str,
        images: list[str] | None = None,
        session_id: str | None = None,
        parent_thread_id: str = "",
        parent_run_id: str = "",
        session_entry=None,
    ) -> dict[str, str]:
        log.info("Gateway.broadcast: agents=%s input=%r", agent_names, text[:80])
        tasks = [
            self.execute(
                name,
                text,
                session_id=session_id or f"broadcast_{name}",
                images=images or [],
                parent_thread_id=parent_thread_id,
                parent_run_id=parent_run_id,
                trigger_type="sub_agent",
                run_kind="child",
                visibility="background",
                session_entry=session_entry,
            )
            for name in agent_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {name: str(r) for name, r in zip(agent_names, results)}
        for name, r in zip(agent_names, results):
            if isinstance(r, Exception):
                log.error("Gateway.broadcast agent=%s raised: %s", name, r)
            else:
                log.info("Gateway.broadcast agent=%s done result=%r", name, str(r)[:80])
        return out

    async def pipeline(
        self,
        agent_names: list[str],
        text: str,
        session_id: str | None = None,
        parent_thread_id: str = "",
        parent_run_id: str = "",
        session_entry=None,
    ) -> str:
        """
        Run ``text`` through a sequence of agents in order.
        Each agent's output becomes the next agent's input.

        A unique pipeline_run_id is generated per invocation so all steps share
        the 'pipeline:{run_id}' memory scope for structured artifact hand-off.
        The pipeline scope is pruned from memory after completion.
        """
        if not agent_names:
            raise ValueError("pipeline requires at least one agent name")
        run_id = make_id("p-")
        import time as _time
        _t0 = _time.monotonic()
        log.info(
            "dispatch: source=pipeline run_id=%s agents=%d",
            run_id[:12], len(agent_names),
        )
        result = text
        _ok = True
        try:
            for name in agent_names:
                log.debug("Pipeline step: agent=%s run_id=%s", name, run_id[:12])
                result = await self.execute(
                    name,
                    result,
                    session_id,
                    pipeline_run_id=run_id,
                    parent_thread_id=parent_thread_id,
                    parent_run_id=parent_run_id,
                    trigger_type="pipeline",
                    run_kind="child",
                    visibility="foreground",
                    session_entry=session_entry,
                )
        except Exception:
            _ok = False
            raise
        finally:
            scope = f"pipeline:{run_id}"
            pruned = 0
            seen_memories: set[int] = set()
            for name in set(agent_names):
                pool = self.get_pool(name)
                mem = pool.memory
                mem_id = id(mem)
                if mem_id in seen_memories:
                    continue
                seen_memories.add(mem_id)
                pruned += mem.delete_by_scope(scope)
            latency_ms = int((_time.monotonic() - _t0) * 1000)
            log.info(
                "dispatch: source=pipeline run_id=%s agents=%d latency_ms=%d ok=%s pruned=%d",
                run_id[:12], len(agent_names), latency_ms, _ok, pruned,
            )
        return result

    async def pipeline_stream(
        self,
        agent_names: list[str],
        text: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        Run pipeline with structured events.
        Yields {"type": "pipeline_step", "agent": name, "output": "..."} per step
        then {"type": "done", "text": final_output}.

        Shares a pipeline_run_id across steps for scoped artifact hand-off.
        Pipeline-scoped memories are pruned after all steps complete.
        """
        if not agent_names:
            raise ValueError("pipeline requires at least one agent name")
        run_id = make_id("p-")
        log.info("Pipeline stream start: run_id=%s agents=%s", run_id[:12], agent_names)
        result = text
        try:
            for name in agent_names:
                log.debug("Pipeline stream step: agent=%s run_id=%s", name, run_id[:12])
                result = await self.execute(name, result, session_id, pipeline_run_id=run_id)
                yield {"type": "pipeline_step", "agent": name, "output": result}
        finally:
            scope = f"pipeline:{run_id}"
            pruned = 0
            seen_memories: set[int] = set()
            for name in set(agent_names):
                pool = self.get_pool(name)
                mem = pool.memory
                mem_id = id(mem)
                if mem_id in seen_memories:
                    continue
                seen_memories.add(mem_id)
                pruned += mem.delete_by_scope(scope)
            log.info("Pipeline stream done: run_id=%s pruned_artifacts=%d", run_id[:12], pruned)
        yield {"type": "done", "text": result}

    def close(self) -> None:
        """Close all non-shared agent resources."""
        seen_memories: set[int] = set()
        for pool in self._pools.values():
            mem_id = id(pool._agent.memory)
            if mem_id not in seen_memories:
                seen_memories.add(mem_id)
                pool._agent.close(close_memory=True)
            else:
                pool._agent.close(close_memory=False)
