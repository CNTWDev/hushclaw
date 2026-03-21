"""Agent collaboration tools bundled as a skill package.

These tools are identical to the built-in agent_tools but are distributed as a
skill package so they can be updated via 'bash install.sh --update' without
requiring a full pip reinstall.

When the system skill_dir is synced by install.sh, these tools are loaded
*without* a namespace (tools_dir parent name is hushclaw-skill-agent-tools but
the tools/ files are loaded directly), so the LLM-visible tool names remain
unchanged: create_agent, delete_agent, etc.
"""
from __future__ import annotations

from hushclaw.tools.base import tool, ToolResult
from hushclaw.util.logging import get_logger

log = get_logger("agent_tools")


@tool(
    name="delegate_to_agent",
    description=(
        "Call another named agent with a task and return its response. "
        "Use this to delegate specialized work to a different agent."
    ),
    timeout=0,
)
async def delegate_to_agent(
    agent_name: str,
    task: str,
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    log.info("delegate_to_agent: agent=%s task=%r", agent_name, task[:80])
    try:
        result = await _gateway.execute(agent_name, task)
        log.info("delegate_to_agent done: agent=%s result=%r", agent_name, (result or "")[:80])
        return ToolResult.ok(result)
    except Exception as e:
        log.error("delegate_to_agent failed: agent=%s error=%s", agent_name, e)
        return ToolResult.error(f"delegate_to_agent failed: {e}")


@tool(
    name="list_agents",
    description="List all available named agents and their descriptions.",
)
def list_agents(_gateway=None) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    agents = _gateway.list_agents()
    if not agents:
        return ToolResult.ok("No agents registered.")
    lines = [f"- {a['name']}: {a['description']}" for a in agents]
    return ToolResult.ok("\n".join(lines))


@tool(
    name="broadcast_to_agents",
    description=(
        "Call multiple agents in parallel with the same task. "
        "Provide agent names as a comma-separated string. "
        "Returns each agent's response."
    ),
    timeout=0,
)
async def broadcast_to_agents(
    agent_names: str,
    task: str,
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    names = [n.strip() for n in agent_names.split(",") if n.strip()]
    if not names:
        return ToolResult.error("No agent names provided.")
    log.info("broadcast_to_agents: agents=%s task=%r", names, task[:80])
    try:
        results = await _gateway.broadcast(names, task)
        log.info("broadcast_to_agents done: agents=%s", names)
        lines = [f"[{name}]: {resp}" for name, resp in results.items()]
        return ToolResult.ok("\n\n".join(lines))
    except Exception as e:
        log.error("broadcast_to_agents failed: %s", e)
        return ToolResult.error(f"broadcast_to_agents failed: {e}")


@tool(
    name="run_pipeline",
    description=(
        "Run a task through a sequence of agents in order. "
        "Each agent's output becomes the next agent's input. "
        "Provide agent names as a comma-separated string (e.g. 'researcher,writer,reviewer')."
    ),
    timeout=0,
)
async def run_pipeline(
    agent_names: str,
    task: str,
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    names = [n.strip() for n in agent_names.split(",") if n.strip()]
    if not names:
        return ToolResult.error("No agent names provided.")
    try:
        result = await _gateway.pipeline(names, task)
        return ToolResult.ok(result)
    except Exception as e:
        return ToolResult.error(f"run_pipeline failed: {e}")


@tool(
    name="create_agent",
    description=(
        "Register a new named agent in the gateway. "
        "Use this to define a specialist agent for later use. "
        "To create an agent AND immediately run a task, use spawn_agent instead."
    ),
)
def create_agent(
    agent_name: str,
    description: str = "",
    model: str = "",
    system_prompt: str = "",
    instructions: str = "",
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    try:
        _gateway.create_agent(
            name=agent_name,
            description=description,
            model=model,
            system_prompt=system_prompt,
            instructions=instructions,
        )
        return ToolResult.ok(f"Agent '{agent_name}' registered successfully.")
    except ValueError as e:
        return ToolResult.error(str(e))


@tool(
    name="delete_agent",
    description="Remove a runtime-created agent from the gateway.",
)
def delete_agent(agent_name: str, _gateway=None) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    try:
        _gateway.delete_agent(agent_name)
        return ToolResult.ok(f"Agent '{agent_name}' removed.")
    except ValueError as e:
        return ToolResult.error(str(e))


@tool(
    name="spawn_agent",
    description=(
        "Create a new agent at runtime and delegate a task to it immediately. "
        "The agent is registered in the gateway and can be reused by name afterward. "
        "Returns the new agent's response to the initial task."
    ),
    timeout=0,
)
async def spawn_agent(
    agent_name: str,
    task: str,
    description: str = "",
    model: str = "",
    system_prompt: str = "",
    instructions: str = "",
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    try:
        _gateway.create_agent(
            name=agent_name,
            description=description,
            model=model,
            system_prompt=system_prompt,
            instructions=instructions,
        )
    except ValueError as e:
        if "already exists" not in str(e):
            return ToolResult.error(str(e))
    try:
        result = await _gateway.execute(agent_name, task)
        return ToolResult.ok(result)
    except Exception as e:
        return ToolResult.error(f"spawn_agent failed: {e}")
