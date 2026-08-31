"""On-demand discovery bridge for long-tail tools."""
from __future__ import annotations

import json
import re

from hushclaw.tools.base import ToolResult, tool, to_api_schema


def _terms(value: str) -> set[str]:
    return {item for item in re.findall(r"[\w-]+", str(value or "").lower()) if item}


@tool(
    name="tool_search",
    description=(
        "Search the runtime tool catalog by capability. Returns exact tool names, descriptions, "
        "and input schemas. Use tool_call to invoke a returned hidden tool."
    ),
    parallel_safe=True,
)
def tool_search(query: str, limit: int = 8, _registry=None) -> ToolResult:
    if _registry is None:
        return ToolResult.error("Tool catalog is unavailable")
    query = str(query or "").strip()
    if not query:
        return ToolResult.error("query is required")
    needles = _terms(query)
    ranked: list[tuple[int, str, object]] = []
    for definition in _registry.list_tools():
        if definition.name in {"tool_search", "tool_call"}:
            continue
        name = str(definition.name or "")
        description = str(definition.description or "")
        name_lower = name.lower()
        haystack = _terms(name.replace("_", " ") + " " + description)
        overlap = len(needles & haystack)
        score = overlap * 10
        if query.lower() in name_lower:
            score += 30
        if query.lower() in description.lower():
            score += 15
        if score <= 0:
            continue
        ranked.append((score, name, definition))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    matches = []
    for _score, _name, definition in ranked[: max(1, min(int(limit or 8), 20))]:
        schema = to_api_schema(definition)
        matches.append({
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["input_schema"],
        })
    return ToolResult.ok(json.dumps({
        "query": query,
        "matches": matches,
        "invoke": {"tool": "tool_call", "arguments": {"name": "<exact name>", "arguments": {}}},
    }, ensure_ascii=False, indent=2))


@tool(
    name="tool_call",
    description=(
        "Invoke a hidden tool returned by tool_search. Pass its exact name and an arguments object "
        "matching the returned input schema. Runtime policy applies to the underlying tool."
    ),
)
def tool_call(name: str, arguments: dict) -> ToolResult:
    # AgentLoop resolves valid bridge calls before ToolRuntime. Reaching this
    # stub means the bridge input was malformed.
    return ToolResult.error(
        "Invalid tool_call bridge input. Use tool_search, then pass an exact non-empty tool name "
        "and a JSON object in arguments."
    )
