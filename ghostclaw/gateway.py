"""Gateway: AgentPool + Gateway for multi-agent routing and session affinity."""
from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import TYPE_CHECKING, AsyncIterator

from ghostclaw.config.schema import AgentDefinition, Config
from ghostclaw.providers.base import Message
from ghostclaw.util.ids import make_id
from ghostclaw.util.logging import get_logger

if TYPE_CHECKING:
    from ghostclaw.agent import Agent
    from ghostclaw.loop import AgentLoop
    from ghostclaw.memory.store import MemoryStore

log = get_logger("gateway")



def _build_agent_from_definition(
    defn: AgentDefinition,
    config: Config,
    shared_memory: "MemoryStore | None" = None,
) -> "Agent":
    """Clone the global Config with per-agent overrides and build an Agent."""
    from ghostclaw.agent import Agent

    agent_cfg = config.agent
    if defn.model:
        agent_cfg = dataclasses.replace(agent_cfg, model=defn.model)
    if defn.system_prompt:
        agent_cfg = dataclasses.replace(agent_cfg, system_prompt=defn.system_prompt)

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
        self._sem = asyncio.Semaphore(max_concurrent)
        self._loops: dict[str, "AgentLoop"] = {}  # session_id → loop
        self._loop_last_used: dict[str, float] = {}  # session_id → unix timestamp
        self._session_ttl = session_ttl_hours * 3600

    def _gc_stale_sessions(self) -> None:
        """Remove AgentLoop entries that haven't been used within the TTL."""
        if self._session_ttl <= 0:
            return
        cutoff = time.time() - self._session_ttl
        stale = [sid for sid, ts in self._loop_last_used.items() if ts < cutoff]
        for sid in stale:
            self._loops.pop(sid, None)
            self._loop_last_used.pop(sid, None)
            log.debug("GC'd stale session: %s", sid[:12])

    def _get_or_create_loop(
        self,
        session_id: str | None,
        gateway: "Gateway | None",
    ) -> "AgentLoop":
        self._gc_stale_sessions()
        if session_id and session_id in self._loops:
            self._loop_last_used[session_id] = time.time()
            return self._loops[session_id]
        loop = self._agent.new_loop(session_id, gateway=gateway)
        if session_id:
            self._loops[session_id] = loop
            self._loop_last_used[session_id] = time.time()
        return loop

    async def execute(
        self,
        text: str,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
    ) -> str:
        async with self._sem:
            loop = self._get_or_create_loop(session_id, gateway)
            return await loop.run(text)

    async def stream(
        self,
        text: str,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
    ) -> AsyncIterator[str]:
        async with self._sem:
            loop = self._get_or_create_loop(session_id, gateway)
            async for chunk in loop.stream_run(text):
                yield chunk

    async def event_stream(
        self,
        text: str,
        session_id: str | None = None,
        gateway: "Gateway | None" = None,
    ) -> AsyncIterator[dict]:
        async with self._sem:
            loop = self._get_or_create_loop(session_id, gateway)
            async for event in loop.event_stream(text):
                yield event

    @property
    def description(self) -> str:
        return self._description


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
        self._build_pools(base_agent)

    def _build_pools(self, base_agent: "Agent") -> None:
        max_c = self._config.gateway.max_concurrent_per_agent
        ttl = self._config.gateway.session_ttl_hours

        # Default pool uses the provided base agent
        self._pools["default"] = AgentPool(base_agent, "default", max_c, "Default agent", ttl)
        self._agent_descriptions["default"] = "Default agent"

        # Enable agent tools on the base agent (gateway is now available)
        base_agent.enable_agent_tools()

        shared_memory = base_agent.memory if self._config.gateway.shared_memory else None

        for defn in self._config.gateway.agents:
            agent = _build_agent_from_definition(defn, self._config, shared_memory)
            agent.enable_agent_tools()
            pool = AgentPool(agent, defn.name, max_c, defn.description, ttl)
            self._pools[defn.name] = pool
            self._agent_descriptions[defn.name] = defn.description
            log.info(
                "Registered agent pool: name=%s model=%s tools=%d",
                defn.name,
                agent.config.agent.model,
                len(agent.registry),
            )

    def create_agent(
        self,
        name: str,
        description: str = "",
        model: str = "",
        system_prompt: str = "",
        instructions: str = "",
    ) -> None:
        """Register a new agent pool at runtime. Raises ValueError if name exists."""
        if name in self._pools:
            raise ValueError(f"Agent '{name}' already exists.")
        if name == "default":
            raise ValueError("'default' is a reserved agent name.")

        defn = AgentDefinition(
            name=name,
            description=description,
            model=model,
            system_prompt=system_prompt,
        )
        shared_memory = self._base_agent.memory if self._config.gateway.shared_memory else None
        agent = _build_agent_from_definition(defn, self._config, shared_memory)
        if instructions:
            agent.config = dataclasses.replace(
                agent.config,
                agent=dataclasses.replace(agent.config.agent, instructions=instructions),
            )
        agent.enable_agent_tools()

        ttl = self._config.gateway.session_ttl_hours
        max_c = self._config.gateway.max_concurrent_per_agent
        pool = AgentPool(agent, name, max_c, description, ttl)
        self._pools[name] = pool
        self._agent_descriptions[name] = description
        log.info("Registered runtime agent: name=%s", name)

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
        return [
            {"name": n, "description": self._agent_descriptions.get(n, "")}
            for n in self._pools
        ]

    async def execute(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
    ) -> str:
        pool = self.get_pool(agent_name)
        return await pool.execute(text, session_id, gateway=self)

    async def stream(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        pool = self.get_pool(agent_name)
        async for chunk in pool.stream(text, session_id, gateway=self):
            yield chunk

    async def event_stream(
        self,
        agent_name: str,
        text: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        pool = self.get_pool(agent_name)
        async for event in pool.event_stream(text, session_id, gateway=self):
            yield event

    async def broadcast(
        self,
        agent_names: list[str],
        text: str,
    ) -> dict[str, str]:
        tasks = [self.execute(name, text) for name in agent_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {name: str(r) for name, r in zip(agent_names, results)}

    async def pipeline(
        self,
        agent_names: list[str],
        text: str,
        session_id: str | None = None,
    ) -> str:
        """
        Run ``text`` through a sequence of agents in order.
        Each agent's output becomes the next agent's input.
        """
        if not agent_names:
            raise ValueError("pipeline requires at least one agent name")
        result = text
        for name in agent_names:
            log.debug("Pipeline step: agent=%s", name)
            result = await self.execute(name, result, session_id)
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
        """
        if not agent_names:
            raise ValueError("pipeline requires at least one agent name")
        result = text
        for name in agent_names:
            log.debug("Pipeline stream step: agent=%s", name)
            result = await self.execute(name, result, session_id)
            yield {"type": "pipeline_step", "agent": name, "output": result}
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
