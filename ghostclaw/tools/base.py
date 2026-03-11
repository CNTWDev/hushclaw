"""@tool decorator and ToolResult dataclass."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolResult:
    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: Any) -> "ToolResult":
        return cls(content=str(content) if not isinstance(content, str) else content)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema
    fn: Callable
    is_async: bool = False
    timeout: int | None = None  # per-tool timeout (overrides global executor timeout)


_PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "NoneType": "null",
}


def _build_schema(fn: Callable) -> dict:
    """Auto-generate JSON Schema from function signature via inspect."""
    sig = inspect.signature(fn)
    props: dict = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls") or name.startswith("_"):
            continue

        ann = param.annotation
        if ann is inspect.Parameter.empty:
            json_type = "string"
        else:
            type_name = ann.__name__ if hasattr(ann, "__name__") else str(ann)
            # Handle Optional[X] / list[X] style annotations
            origin = getattr(ann, "__origin__", None)
            if origin is list:
                json_type = "array"
            elif origin is dict:
                json_type = "object"
            elif origin is not None and hasattr(origin, "__name__") and origin.__name__ == "Union":
                # Optional[X] — try to get inner type
                args = [a for a in ann.__args__ if a is not type(None)]
                if args:
                    inner = args[0]
                    inner_name = inner.__name__ if hasattr(inner, "__name__") else "str"
                    json_type = _PYTHON_TO_JSON_TYPE.get(inner_name, "string")
                else:
                    json_type = "string"
            else:
                json_type = _PYTHON_TO_JSON_TYPE.get(type_name, "string")

        prop: dict = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default is not None:
            prop["default"] = param.default

        props[name] = prop

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def tool(
    name: str | None = None,
    description: str = "",
    timeout: int | None = None,
) -> Callable:
    """Decorator that registers a function as a GhostClaw tool."""
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip()
        schema = _build_schema(fn)
        is_async = inspect.iscoroutinefunction(fn)

        fn._ghostclaw_tool = ToolDefinition(
            name=tool_name,
            description=tool_desc,
            parameters=schema,
            fn=fn,
            is_async=is_async,
            timeout=timeout,
        )
        return fn

    return decorator


def to_api_schema(td: ToolDefinition) -> dict:
    """Convert ToolDefinition to Anthropic/OpenAI tool schema format."""
    return {
        "name": td.name,
        "description": td.description,
        "input_schema": td.parameters,
    }
