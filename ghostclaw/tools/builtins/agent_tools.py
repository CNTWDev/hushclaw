"""Agent collaboration tools: delegate tasks between named agents."""
from __future__ import annotations

from ghostclaw.tools.base import tool, ToolResult


@tool(
    name="delegate_to_agent",
    description=(
        "Call another named agent with a task and return its response. "
        "Use this to delegate specialized work to a different agent."
    ),
)
async def delegate_to_agent(
    agent_name: str,
    task: str,
    _gateway=None,
) -> ToolResult:
    if _gateway is None:
        return ToolResult.error("Gateway not available — not running in multi-agent mode.")
    try:
        result = await _gateway.execute(agent_name, task)
        return ToolResult.ok(result)
    except Exception as e:
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
    try:
        results = await _gateway.broadcast(names, task)
        lines = [f"[{name}]: {resp}" for name, resp in results.items()]
        return ToolResult.ok("\n\n".join(lines))
    except Exception as e:
        return ToolResult.error(f"broadcast_to_agents failed: {e}")


@tool(
    name="run_pipeline",
    description=(
        "Run a task through a sequence of agents in order. "
        "Each agent's output becomes the next agent's input. "
        "Provide agent names as a comma-separated string (e.g. 'researcher,writer,reviewer')."
    ),
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
    name="spawn_agent",
    description=(
        "Create a new agent at runtime and delegate a task to it immediately. "
        "The agent is registered in the gateway and can be reused by name afterward. "
        "Returns the new agent's response to the initial task."
    ),
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
