---
name: hushclaw-agent-tools
description: Agent collaboration tools for multi-agent orchestration (create, delete, spawn, delegate, broadcast, pipeline)
tags: ["agents", "multi-agent", "orchestration", "gateway"]
author: HushClaw
version: "1.0.0"
has_tools: true
---

You have access to agent collaboration tools that let you orchestrate multi-agent workflows:

- **create_agent** — Register a new named specialist agent in the gateway
- **delete_agent** — Remove a runtime-created agent
- **spawn_agent** — Create an agent and immediately delegate a task to it
- **delegate_to_agent** — Send a task to an existing named agent and get its response
- **broadcast_to_agents** — Call multiple agents in parallel with the same task
- **run_pipeline** — Run a task sequentially through a chain of agents
- **list_agents** — List all registered agents and their descriptions

Use these tools when you need to break complex tasks into specialized sub-tasks handled by different agents.
