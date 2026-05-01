"""Gateway: AgentPool + Gateway for multi-agent routing and session affinity."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from hushclaw.config.schema import AgentDefinition, Config
from hushclaw.providers.base import Message
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.agent import Agent
    from hushclaw.loop import AgentLoop
    from hushclaw.memory.store import MemoryStore

log = get_logger("gateway")
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# ── Org-context injection helpers ────────────────────────────────────────────

_ORG_CONTEXT_MARKER = "\n\n<!-- [hushclaw:org-context] -->\n"


def _strip_org_context(instructions: str) -> str:
    """Remove the auto-generated org-context block from instructions."""
    if _ORG_CONTEXT_MARKER in instructions:
        return instructions.split(_ORG_CONTEXT_MARKER)[0]
    return instructions


def _build_org_context_str(
    name: str,
    description: str,
    role: str,
    capabilities: list[str],
    reports_to: str,
    all_meta: "dict[str, dict]",
) -> str:
    """Return a formatted org-context block that describes the agent's identity
    and (for commanders) its direct reports with delegation guidance."""
    role_lower = (role or "specialist").lower()
    lines = ["## Your Agent Identity"]
    if description:
        lines.append(f"You are **{name}** — {description}.")
    else:
        lines.append(f"You are the **{name}** agent.")
    lines.append(f"Org role: {role_lower}")
    if capabilities:
        lines.append(f"Capabilities: {', '.join(capabilities)}")
    if reports_to:
        lines.append(f"Reports to: **{reports_to}**")

    direct_reports = sorted(
        [(n, m) for n, m in all_meta.items()
         if (m.get("reports_to") or "").strip() == name],
        key=lambda x: x[0],
    )
    if direct_reports:
        lines.append("\n## Your Direct Reports")
        for dr_name, dr_meta in direct_reports:
            dr_desc = (dr_meta.get("description") or "").strip()
            dr_caps = dr_meta.get("capabilities") or []
            entry = f"- **{dr_name}**"
            if dr_desc:
                entry += f": {dr_desc}"
            if dr_caps:
                entry += f" (capabilities: {', '.join(dr_caps)})"
            lines.append(entry)
        lines.append(
            "\n## Delegation Guidance\n"
            "**Default: handle the task yourself.** Only delegate when the task explicitly "
            "requires a capability you do not have — and only to the specific direct report "
            "whose capabilities match. Do NOT delegate for general questions or tasks you "
            "can answer directly. Never involve more agents than the task strictly requires.\n"
            "Delegation tools (use sparingly):\n"
            "- `delegate_to_agent(agent_name, task)` — one specialist, one task that needs their specific capability.\n"
            "- `broadcast_to_agents(\"name1,name2\", task)` — only when each named specialist "
            "must contribute a distinct, non-overlapping portion.\n"
            f"- `run_hierarchical(\"{name}\", task)` — reserved for explicit org-wide coordination; "
            "never use for ordinary requests.\n"
            "When in doubt, do the work yourself."
        )
    return "\n".join(lines)


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
        asyncio.create_task(loop.aclose())

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
                effective_thread_id = self.memory.get_or_create_thread(
                    resolved_session_id,
                    agent_name=self.name,
                )
            cache_key = self._loop_cache_key(resolved_session_id, effective_thread_id)
            loop = self._get_or_create_loop(resolved_session_id, effective_thread_id, gateway)
            loop.pipeline_run_id = pipeline_run_id
            if client_now:
                loop.executor.set_context(_client_now=client_now)

            # Phase 3: create/reuse thread and open a run for this execution.
            _sid = loop.session_id
            memory = loop.memory
            trigger = "pipeline" if pipeline_run_id else "user"
            run_id = memory.create_run(effective_thread_id, _sid, trigger_type=trigger)
            memory.events.append(
                _sid, "run_started",
                {"agent": self.name, "trigger": trigger},
                thread_id=effective_thread_id, run_id=run_id,
            )

            try:
                async for event in loop.event_stream(
                    text, images=images or [], workspace_dir=workspace_dir, workspace_name=workspace_name,
                    thread_id=effective_thread_id, run_id=run_id,
                    references=references or [],
                ):
                    yield event
                memory.complete_run(run_id)
                memory.events.append(
                    _sid, "run_completed", {},
                    thread_id=effective_thread_id, run_id=run_id,
                )
            except Exception:
                memory.fail_run(run_id)
                memory.events.append(
                    _sid, "run_failed", {},
                    thread_id=effective_thread_id, run_id=run_id,
                )
                raise
            finally:
                loop.pipeline_run_id = ""
                # Close sandbox for ephemeral loops (not in the pool).
                # Pooled loops are closed by _gc_stale_sessions() on TTL expiry.
                if cache_key not in self._loops:
                    asyncio.create_task(loop.aclose())

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
        self._pools: dict[str, AgentPool] = {}
        self._agent_descriptions: dict[str, str] = {}
        self._agent_meta: dict[str, dict] = {}
        self._runtime_defs: list[dict] = []  # persisted dynamic agent definitions
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
        for pool in self._pools.values():
            pool.set_scheduler(scheduler)

    def clear_all_cached_loops(self) -> None:
        for pool in self._pools.values():
            pool.clear_cached_loops()

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

    def _inject_org_context(
        self,
        agent: "Agent",
        name: str,
        description: str,
        role: str,
        capabilities: list[str],
        reports_to: str,
    ) -> None:
        """Append a fresh org-context block to agent's instructions (idempotent)."""
        base = _strip_org_context(agent.config.agent.instructions)
        org_ctx = _build_org_context_str(
            name, description, role, capabilities, reports_to, self._agent_meta
        )
        new_instr = base + _ORG_CONTEXT_MARKER + org_ctx
        agent.config = dataclasses.replace(
            agent.config,
            agent=dataclasses.replace(agent.config.agent, instructions=new_instr),
        )

    def _refresh_parent_org_context(self, parent_name: str) -> None:
        """Rebuild a parent agent's org-context block after its children changed."""
        if not parent_name or parent_name not in self._pools:
            return
        pool = self._pools[parent_name]
        meta = self._agent_meta.get(parent_name, {})
        self._inject_org_context(
            pool._agent,
            name=parent_name,
            description=self._agent_descriptions.get(parent_name, ""),
            role=meta.get("role", "specialist"),
            capabilities=meta.get("capabilities", []),
            reports_to=meta.get("reports_to", ""),
        )
        pool.clear_cached_loops()
        log.debug("Refreshed org context for agent: %s", parent_name)

    def _build_pools(self, base_agent: "Agent") -> None:
        max_c = self._config.gateway.max_concurrent_per_agent
        ttl = self._config.gateway.session_ttl_hours

        # Default pool uses the provided base agent
        self._pools["default"] = AgentPool(base_agent, "default", max_c, "Default agent", ttl)
        self._agent_descriptions["default"] = "Default agent"
        self._agent_meta["default"] = {
            "role": "commander",
            "team": "default",
            "reports_to": "",
            "capabilities": [],
        }

        # Enable agent tools on the base agent (gateway is now available)
        base_agent.enable_agent_tools()

        shared_memory = base_agent.memory if self._config.gateway.shared_memory else None

        # First pass: build all pools and register meta.
        for defn in self._config.gateway.agents:
            agent = _build_agent_from_definition(defn, self._config, shared_memory)
            agent.enable_agent_tools()
            pool = AgentPool(agent, defn.name, max_c, defn.description, ttl)
            self._pools[defn.name] = pool
            self._agent_descriptions[defn.name] = defn.description
            self._agent_meta[defn.name] = {
                "role": (defn.role or "specialist"),
                "team": defn.team or "",
                "reports_to": defn.reports_to or "",
                "capabilities": list(defn.capabilities or []),
            }
            log.info(
                "Registered agent pool: name=%s model=%s tools=%d",
                defn.name,
                agent.config.agent.model,
                len(agent.registry),
            )

        # Second pass: inject org context now that all _agent_meta is populated,
        # so commanders see their full list of direct reports.
        for defn in self._config.gateway.agents:
            self._inject_org_context(
                self._pools[defn.name]._agent,
                name=defn.name,
                description=defn.description,
                role=defn.role or "specialist",
                capabilities=list(defn.capabilities or []),
                reports_to=defn.reports_to or "",
            )

    @staticmethod
    def _normalize_role(role: str | None) -> str:
        raw = (role or "specialist").strip().lower()
        # Accept common LLM/user synonyms and normalize to canonical roles.
        aliases = {
            "leader": "commander",
            "manager": "commander",
            "supervisor": "commander",
            "director": "commander",
            "expert": "specialist",
            "worker": "specialist",
            "member": "specialist",
            "agent": "specialist",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_capabilities(capabilities: list[str] | str | None) -> list[str]:
        if not capabilities:
            return []
        # Guard: LLM may pass a comma-separated string when the JSON schema was
        # incorrectly inferred as "string" instead of "array".  Split on commas
        # so we don't iterate the string character-by-character.
        if isinstance(capabilities, str):
            capabilities = [s for s in (s.strip() for s in capabilities.split(",")) if s]
        out: list[str] = []
        seen: set[str] = set()
        for c in capabilities:
            val = (c or "").strip()
            if val and val not in seen:
                seen.add(val)
                out.append(val)
        return out

    def _validate_role(self, role: str) -> None:
        if role not in {"commander", "specialist"}:
            raise ValueError(
                "role must be one of: commander, specialist "
                "(aliases: leader/manager -> commander, expert/agent -> specialist)"
            )

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

    def _validate_hierarchy(self, mapping: dict[str, str], *, updating: str | None = None) -> None:
        for agent_name, parent in mapping.items():
            if not parent:
                continue
            if parent == agent_name:
                raise ValueError(f"reports_to for '{agent_name}' cannot point to itself.")
            # Forward references (parent not yet registered) are intentionally allowed;
            # the parent's org-context will pick up the child when it is created.

        # Cycle detection only among already-known agents; skip forward refs.
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError(f"Hierarchy cycle detected at '{node}'.")
            visiting.add(node)
            parent = mapping.get(node, "")
            if parent and parent in mapping:
                dfs(parent)
            visiting.remove(node)
            visited.add(node)

        for name in mapping:
            dfs(name)

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
                    role=d.get("role", "specialist"),
                    team=d.get("team", ""),
                    reports_to=d.get("reports_to", ""),
                    capabilities=d.get("capabilities", []),
                    tools=d.get("tools", []),
                )
                self._runtime_defs.append({
                    "name": name,
                    "description": d.get("description", ""),
                    "system_prompt": d.get("system_prompt", ""),
                    "instructions": d.get("instructions", ""),
                    "role": self._normalize_role(d.get("role", "specialist")),
                    "team": d.get("team", "") or "",
                    "reports_to": d.get("reports_to", "") or "",
                    "capabilities": self._normalize_capabilities(d.get("capabilities", [])),
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
        role: str = "specialist",
        team: str = "",
        reports_to: str = "",
        capabilities: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        """Internal: build and register an agent pool (no persistence side-effects)."""
        norm_role = self._normalize_role(role)
        self._validate_role(norm_role)
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
            "role": norm_role,
            "team": team or "",
            "reports_to": reports_to or "",
            "capabilities": self._normalize_capabilities(capabilities),
        }
        # Inject org context — _agent_meta is now updated so direct-report
        # lookup for pre-existing children of this agent works correctly.
        self._inject_org_context(
            agent, name, description, norm_role,
            self._normalize_capabilities(capabilities), reports_to or "",
        )
        # If any agents were created before this one with reports_to=name (forward
        # references), their own org context already says "Reports to: name" but
        # they missed the parent's direct-report refresh.  Re-inject their context
        # now so the hierarchy description stays consistent on both sides.
        for child_name, child_meta in list(self._agent_meta.items()):
            if child_name == name:
                continue
            if (child_meta.get("reports_to") or "") == name:
                child_pool = self._pools.get(child_name)
                if child_pool is not None:
                    self._inject_org_context(
                        child_pool._agent,
                        child_name,
                        child_meta.get("description", ""),
                        child_meta.get("role", "specialist"),
                        child_meta.get("capabilities", []),
                        name,
                    )

    def create_agent(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        instructions: str = "",
        role: str = "specialist",
        team: str = "",
        reports_to: str = "",
        capabilities: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        """Register a new agent pool at runtime and persist it across restarts."""
        name = self._validate_agent_name(name)
        if name in self._pools:
            raise ValueError(f"Agent '{name}' already exists.")
        role = self._normalize_role(role)
        self._validate_role(role)
        new_mapping = {n: (m.get("reports_to", "") if m else "") for n, m in self._agent_meta.items()}
        new_mapping[name] = reports_to or ""
        self._validate_hierarchy(new_mapping)

        self._register_agent(
            name=name,
            description=description,
            system_prompt=system_prompt,
            instructions=instructions,
            role=role,
            team=team,
            reports_to=reports_to,
            capabilities=capabilities,
            tools=tools,
        )
        self._runtime_defs.append({
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "instructions": instructions,
            "role": role,
            "team": team or "",
            "reports_to": reports_to or "",
            "capabilities": self._normalize_capabilities(capabilities),
            "tools": tools or [],
        })
        self._save_dynamic_agents()
        # Agent topology changes invalidate cached conversational assumptions
        # such as prior list_agents results and org-context snapshots.
        self.clear_all_cached_loops()
        # Refresh parent's org context so it learns about its new direct report.
        if reports_to and reports_to in self._pools:
            self._refresh_parent_org_context(reports_to)
        log.info("Registered runtime agent: name=%s", name)

    def delete_agent(self, name: str) -> None:
        """Remove a runtime-created agent. Cannot delete 'default' or config-defined agents."""
        if name == "default":
            raise ValueError("Cannot delete the default agent.")
        if name not in self._pools:
            raise ValueError(f"Agent '{name}' not found.")
        old_parent = (self._agent_meta.get(name) or {}).get("reports_to", "")
        del self._pools[name]
        del self._agent_descriptions[name]
        self._agent_meta.pop(name, None)
        self._runtime_defs = [d for d in self._runtime_defs if d["name"] != name]
        self._save_dynamic_agents()
        # Clear all cached loops so no live session keeps stale agent topology
        # or previously cached list_agents/tool results in memory.
        self.clear_all_cached_loops()
        # Refresh parent's org context so the deleted agent no longer appears.
        if old_parent and old_parent in self._pools:
            self._refresh_parent_org_context(old_parent)
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
                "role": self._normalize_role(d.get("role", "specialist")),
                "team": d.get("team", "") or "",
                "reports_to": d.get("reports_to", "") or "",
                "capabilities": self._normalize_capabilities(d.get("capabilities", [])),
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
            # Strip auto-generated org context so callers see only user-defined instructions.
            "instructions": _strip_org_context(cfg.agent.instructions),
            "role": self._normalize_role(meta.get("role", "specialist")),
            "team": meta.get("team", "") or "",
            "reports_to": meta.get("reports_to", "") or "",
            "capabilities": self._normalize_capabilities(meta.get("capabilities", [])),
            "tools": list(cfg.tools.enabled) if cfg.tools.enabled else [],
            "editable": False,
        }

    def update_agent(
        self,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        instructions: str | None = None,
        role: str | None = None,
        team: str | None = None,
        reports_to: str | None = None,
        capabilities: list[str] | None = None,
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
        old_reports_to = (self._agent_meta.get(name) or {}).get("reports_to", "")
        d.pop("model", None)
        if description is not None:
            d["description"] = description
        if system_prompt is not None:
            d["system_prompt"] = system_prompt
        if instructions is not None:
            # Store only user-specified instructions; org context is always re-generated.
            d["instructions"] = _strip_org_context(instructions)
        if role is not None:
            d["role"] = self._normalize_role(role)
            self._validate_role(d["role"])
        if team is not None:
            d["team"] = team or ""
        if reports_to is not None:
            d["reports_to"] = reports_to or ""
        if capabilities is not None:
            d["capabilities"] = self._normalize_capabilities(capabilities)
        if tools is not None:
            d["tools"] = tools
        new_mapping = {n: (m.get("reports_to", "") if m else "") for n, m in self._agent_meta.items()}
        new_mapping[name] = d.get("reports_to", "") or ""
        self._validate_hierarchy(new_mapping)
        # Tear down existing pool and re-register with updated definition
        del self._pools[name]
        del self._agent_descriptions[name]
        self._agent_meta.pop(name, None)
        self._register_agent(
            name=name,
            description=d.get("description", ""),
            system_prompt=d.get("system_prompt", ""),
            instructions=d.get("instructions", ""),
            role=d.get("role", "specialist"),
            team=d.get("team", ""),
            reports_to=d.get("reports_to", ""),
            capabilities=d.get("capabilities", []),
            tools=d.get("tools", []),
        )
        self._save_dynamic_agents()
        # Updating agent metadata changes org context and list_agents output.
        self.clear_all_cached_loops()
        # Refresh org context for any parent agents affected by hierarchy change.
        new_reports_to = d.get("reports_to", "") or ""
        for parent in {old_reports_to, new_reports_to} - {"", name}:
            if parent in self._pools:
                self._refresh_parent_org_context(parent)
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
                "role": self._normalize_role(self._agent_meta.get(n, {}).get("role", "specialist")),
                "team": self._agent_meta.get(n, {}).get("team", "") or "",
                "reports_to": self._agent_meta.get(n, {}).get("reports_to", "") or "",
                "capabilities": self._normalize_capabilities(self._agent_meta.get(n, {}).get("capabilities", [])),
                "editable": n in runtime_names,
            }
            for n in self._pools
        ]

    async def execute_hierarchical(
        self,
        commander_name: str,
        text: str,
        mode: str = "parallel",
        session_id: str | None = None,
    ) -> str:
        known = list(self._pools.keys())
        if commander_name not in self._pools:
            suggestion = f" Known agents: {known}." if known else ""
            raise ValueError(
                f"Agent '{commander_name}' not found.{suggestion}"
            )
        meta = self._agent_meta.get(commander_name, {})
        if self._normalize_role(meta.get("role", "specialist")) != "commander":
            raise ValueError(
                f"Agent '{commander_name}' has role '{meta.get('role', 'specialist')}', "
                f"not 'commander'. Use update_agent(name='{commander_name}', role='commander') "
                f"to promote it first."
            )
        children = [
            a["name"] for a in self.list_agents()
            if (a.get("reports_to") or "") == commander_name
        ]
        if not children:
            all_agents = [a["name"] for a in self.list_agents() if a["name"] != commander_name]
            example = all_agents[0] if all_agents else "specialist-agent"
            raise ValueError(
                f"Commander '{commander_name}' has no direct reports. "
                f"Assign at least one agent with: "
                f"update_agent(name='{example}', reports_to='{commander_name}')"
            )
        mode = (mode or "parallel").lower()
        if mode not in {"parallel", "sequential"}:
            raise ValueError("mode must be 'parallel' or 'sequential'")
        import time as _time
        _t0 = _time.monotonic()
        log.info(
            "dispatch: source=run_hierarchical commander=%s mode=%s children=%d",
            commander_name, mode, len(children),
        )
        try:
            if mode == "parallel":
                outputs = await self.broadcast(children, text)
                lines = [f"## Hierarchical Dispatch ({commander_name})", f"Mode: {mode}", ""]
                for child in children:
                    lines.append(f"### {child}")
                    lines.append(outputs.get(child, ""))
                    lines.append("")
                result = "\n".join(lines).strip()
            else:
                # sequential mode
                raw = await self.pipeline(children, text, session_id=session_id)
                result = (
                    f"## Hierarchical Dispatch ({commander_name})\n"
                    f"Mode: {mode}\n\n"
                    f"Sequence: {', '.join(children)}\n\n"
                    f"### Final Synthesis\n{raw}"
                )
            latency_ms = int((_time.monotonic() - _t0) * 1000)
            log.info(
                "dispatch: source=run_hierarchical commander=%s mode=%s children=%d latency_ms=%d ok=True",
                commander_name, mode, len(children), latency_ms,
            )
            return result
        except Exception:
            latency_ms = int((_time.monotonic() - _t0) * 1000)
            log.info(
                "dispatch: source=run_hierarchical commander=%s mode=%s children=%d latency_ms=%d ok=False",
                commander_name, mode, len(children), latency_ms,
            )
            raise

    async def execute(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
        thread_id: str | None = None,
        pipeline_run_id: str = "",
        images: list[str] | None = None,
    ) -> str:
        if session_id is None and thread_id is None:
            session_id = self._implicit_session_id(agent_name)
        log.info("Gateway.execute: agent=%s session=%s input=%r",
                 agent_name, (session_id or "")[:12], text[:80])
        pool = self.get_pool(agent_name)
        result = await pool.execute(
            text,
            session_id,
            thread_id=thread_id,
            gateway=self,
            pipeline_run_id=pipeline_run_id,
            images=images or [],
        )
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
            images=images or [],
            workspace_dir=workspace_dir,
            workspace_name=workspace or "",
            client_now=client_now,
            references=references or [],
        ):
            yield event

    async def broadcast(
        self,
        agent_names: list[str],
        text: str,
        images: list[str] | None = None,
    ) -> dict[str, str]:
        log.info("Gateway.broadcast: agents=%s input=%r", agent_names, text[:80])
        tasks = [self.execute(name, text, session_id=f"broadcast_{name}", images=images or []) for name in agent_names]
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
                result = await self.execute(name, result, session_id, pipeline_run_id=run_id)
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
