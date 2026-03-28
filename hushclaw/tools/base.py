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


def _resolve_json_type(ann) -> str:
    """Recursively resolve a Python type annotation to a JSON Schema type string.

    Handles all three union styles:
      - ``typing.Optional[X]``   (origin is typing.Union, NoneType in __args__)
      - ``typing.Union[X, None]`` (same as above)
      - ``X | None``             (Python 3.10+ types.UnionType, no __origin__)
    Also handles generic aliases like ``list[str]``.
    """
    import types as _types

    origin = getattr(ann, "__origin__", None)

    # Plain list / dict generic aliases: list[str], dict[str, int], etc.
    if origin is list:
        return "array"
    if origin is dict:
        return "object"

    # typing.Union / typing.Optional  (origin == typing.Union)
    if origin is not None and getattr(origin, "__name__", None) == "Union":
        args = [a for a in ann.__args__ if a is not type(None)]
        return _resolve_json_type(args[0]) if args else "string"

    # Python 3.10+ ``X | Y`` union syntax (types.UnionType — no __origin__)
    if isinstance(ann, _types.UnionType):  # type: ignore[attr-defined]
        args = [a for a in ann.__args__ if a is not type(None)]
        return _resolve_json_type(args[0]) if args else "string"

    # Plain types: str, int, float, bool, list, dict
    type_name = ann.__name__ if hasattr(ann, "__name__") else str(ann)
    return _PYTHON_TO_JSON_TYPE.get(type_name, "string")


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
            json_type = _resolve_json_type(ann)

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
    """Decorator that registers a function as a HushClaw tool."""
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip()
        schema = _build_schema(fn)
        is_async = inspect.iscoroutinefunction(fn)

        fn._hushclaw_tool = ToolDefinition(
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
