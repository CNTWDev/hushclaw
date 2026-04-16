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
    parallel_safe: bool = False  # True = read-only, no shared state mutation; safe for asyncio.gather


_PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "NoneType": "null",
}


def _str_ann_to_prop(ann_str: str) -> dict:
    """Parse a *string* annotation to a JSON Schema property dict.

    Handles:
    - Bare types: ``str``, ``int``, ``list``, ``dict``, ``bool``, ``float``
    - Generic lists: ``list[str]``, ``list[int]``, …
    - Union/Optional: ``X | None``, ``Optional[X]``
    """
    s = ann_str.strip()

    # Strip outer Optional[…]
    if s.startswith("Optional[") and s.endswith("]"):
        return _str_ann_to_prop(s[9:-1])

    # Union: "X | None" or "None | X" — keep the non-None side
    if "|" in s:
        parts = [p.strip() for p in s.split("|")]
        non_none = [p for p in parts if p.lower() not in ("none", "nonetype")]
        if non_none:
            return _str_ann_to_prop(non_none[0])

    # Generic list[X]
    if s.startswith("list[") and s.endswith("]"):
        item_str = s[5:-1].strip()
        return {"type": "array", "items": _str_ann_to_prop(item_str)}

    # Bare list
    if s == "list":
        return {"type": "array", "items": {}}

    # dict (bare or generic)
    if s == "dict" or (s.startswith("dict[") and s.endswith("]")):
        return {"type": "object"}

    # Scalar types
    return {"type": _PYTHON_TO_JSON_TYPE.get(s, "string")}


def _resolve_prop(ann) -> dict:
    """Return a full JSON Schema property dict for a Python type annotation.

    Handles real type objects (post-eval) and string annotations (produced by
    ``from __future__ import annotations``).

    For array types always emits an ``"items"`` sub-schema so that
    OpenAI-compatible APIs (which strictly validate JSON Schema) don't
    reject the tool definition with "array schema missing items".
    """
    # String annotation — use lightweight string parser
    if isinstance(ann, str):
        return _str_ann_to_prop(ann)

    import types as _types

    origin = getattr(ann, "__origin__", None)

    # Generic list alias: list[str], list[int], list[Any], …
    if origin is list:
        args = getattr(ann, "__args__", None)
        items = _resolve_prop(args[0]) if args else {}
        return {"type": "array", "items": items}

    # Generic dict alias
    if origin is dict:
        return {"type": "object"}

    # typing.Union / typing.Optional  (origin == typing.Union)
    if origin is not None and getattr(origin, "__name__", None) == "Union":
        args = [a for a in ann.__args__ if a is not type(None)]
        return _resolve_prop(args[0]) if args else {"type": "string"}

    # Python 3.10+ ``X | Y`` union syntax (types.UnionType — no __origin__)
    if isinstance(ann, _types.UnionType):  # type: ignore[attr-defined]
        args = [a for a in ann.__args__ if a is not type(None)]
        return _resolve_prop(args[0]) if args else {"type": "string"}

    # Plain bare ``list`` or ``dict`` (no type args)
    if ann is list:
        return {"type": "array", "items": {}}
    if ann is dict:
        return {"type": "object"}

    # Plain scalar types: str, int, float, bool, …
    type_name = ann.__name__ if hasattr(ann, "__name__") else str(ann)
    return {"type": _PYTHON_TO_JSON_TYPE.get(type_name, "string")}


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
            prop: dict = {"type": "string"}
        else:
            # param.annotation may be a string (from __future__ import annotations)
            # or a real type object — _resolve_prop handles both.
            prop = _resolve_prop(ann)

        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default is not None:
            prop = {**prop, "default": param.default}

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
    parallel_safe: bool = False,
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
            parallel_safe=parallel_safe,
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
