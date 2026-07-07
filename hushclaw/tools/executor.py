"""Async tool executor with timeout and error isolation."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

from hushclaw.tools.base import ToolDefinition, ToolResult
from hushclaw.tools.runtime_context import ToolRuntimeContext
from hushclaw.runtime.tool_output_budget import apply_tool_output_budget
from hushclaw.runtime.threat_patterns import wrap_untrusted_context
from hushclaw.util.logging import get_logger

log = get_logger("tools.executor")


class ToolExecutor:
    def __init__(self, registry, timeout: int = 30) -> None:
        self.registry = registry
        self.timeout = timeout
        self._context: dict[str, Any] = {}
        self._runtime_context: ToolRuntimeContext | None = None

    def set_context(self, **kwargs: Any) -> None:
        """Inject context objects (e.g. memory_store, config) for tools."""
        self._context.update(kwargs)
        if self._runtime_context is not None:
            for key, value in kwargs.items():
                self._runtime_context.set_extra(key, value)

    def set_runtime_context(self, runtime_context: ToolRuntimeContext) -> None:
        """Attach the typed runtime context used by ToolRuntime."""
        self._runtime_context = runtime_context
        for key, value in self._context.items():
            runtime_context.set_extra(key, value)

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Return a context value by key (public accessor, avoids private _context access)."""
        if self._runtime_context is not None:
            value = self._runtime_context.get(key, default)
            if value is not default:
                return value
        return self._context.get(key, default)

    async def execute_single(self, name: str, arguments: dict) -> ToolResult:
        """Execute a single tool call, identical to :meth:`execute` but intended
        for direct (non-LLM-driven) invocations such as REPL ``direct_tool`` dispatch."""
        return await self.execute(name, arguments)

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        name, arguments = self._normalize_tool_call(name, arguments or {})
        td: ToolDefinition | None = self.registry.get(name)
        if td is None:
            return ToolResult.error(f"Unknown tool: {name!r}")

        # Inject context variables that the function accepts
        sig = inspect.signature(td.fn)
        kwargs = dict(arguments or {})
        if self._runtime_context is not None and "_runtime" in sig.parameters:
            kwargs["_runtime"] = self._runtime_context
        context_items = dict(self._context)
        if self._runtime_context is not None:
            context_items.update(self._runtime_context.legacy_items())
        for ctx_key, ctx_val in context_items.items():
            if ctx_key in sig.parameters:
                kwargs[ctx_key] = ctx_val
        kwargs = self._normalize_kwargs(sig, kwargs, name)

        missing = [
            pname for pname, param in sig.parameters.items()
            if pname not in ("self", "cls")
            and not pname.startswith("_")
            and param.default is inspect.Parameter.empty
            and param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            and pname not in kwargs
        ]
        if missing:
            return ToolResult.error(
                f"Tool {name!r} missing required argument(s): {', '.join(missing)}"
            )

        # Per-tool timeout overrides the global executor timeout. Sub-agent tools
        # use gateway-level lease / progress monitoring instead of a fixed hard
        # timeout so long-running but healthy child runs can continue.
        effective_timeout = td.timeout if td.timeout is not None else self.timeout
        use_timeout = effective_timeout > 0

        try:
            if td.is_async:
                coro = td.fn(**kwargs)
                result = await (asyncio.wait_for(coro, timeout=effective_timeout) if use_timeout else coro)
            else:
                loop = asyncio.get_event_loop()
                fut = loop.run_in_executor(None, lambda: td.fn(**kwargs))
                result = await (asyncio.wait_for(fut, timeout=effective_timeout) if use_timeout else fut)
        except asyncio.TimeoutError:
            log.warning("Tool %s timed out after %ss", name, effective_timeout)
            return ToolResult.error(f"Tool {name!r} timed out after {effective_timeout}s")
        except asyncio.CancelledError:
            raise  # must re-raise so asyncio task management works correctly
        except Exception as e:
            log.error("Tool %s raised %s: %s", name, type(e).__name__, e, exc_info=True)
            return ToolResult.error(f"Tool {name!r} raised {type(e).__name__}: {e}")

        if isinstance(result, ToolResult):
            final_result = result
        else:
            final_result = ToolResult.ok(result)

        try:
            original_len = len(final_result.content or "")
            final_result = apply_tool_output_budget(
                final_result,
                tool_name=name,
                memory=self.get_context_value("_memory_store"),
                session_id=str(self.get_context_value("_session_id") or ""),
            )
            if final_result.artifact_id:
                log.debug(
                    "tool %s: offloaded %d chars to artifact %s",
                    name,
                    original_len,
                    final_result.artifact_id,
                )
        except Exception as exc:
            log.warning("tool output budget failed for tool %s: %s", name, exc)

        if final_result.content:
            wrapped, scan = wrap_untrusted_context(
                final_result.content,
                source=f"tool:{name}",
                kind="tool_result",
                trusted=False,
            )
            metadata = dict(final_result.metadata or {})
            metadata["threat_labels"] = list(scan.labels)
            metadata["instruction_boundary"] = "untrusted_context"
            final_result = ToolResult(
                content=wrapped,
                is_error=final_result.is_error,
                artifact_id=final_result.artifact_id,
                metadata=metadata,
            )

        return final_result

    @staticmethod
    def _normalize_tool_call(name: str, arguments: dict) -> tuple[str, dict]:
        """Normalize high-value model mistakes before registry lookup."""
        tool_name = str(name or "").strip()
        args = dict(arguments or {})

        ops = args.get("operations")
        if isinstance(ops, list):
            for op in ops:
                if isinstance(op, dict) and "op_type" in op and "type" not in op:
                    op["type"] = op.pop("op_type")
        return tool_name, args

    @staticmethod
    def _normalize_kwargs(sig: inspect.Signature, kwargs: dict, tool_name: str) -> dict:
        """Defensive normalization for model-generated tool arguments.

        - Accept common aliases (queries/keywords -> query, top_k/k -> limit)
        - Drop unknown keys for tools that do not accept **kwargs
        """
        out = dict(kwargs)
        params = sig.parameters

        # query aliases
        if "query" in params and "query" not in out:
            for alias in ("queries", "keywords", "keyword", "search_query", "question", "text", "skill_name"):
                if alias not in out:
                    continue
                v = out.get(alias)
                if isinstance(v, list):
                    q = " ".join(str(x).strip() for x in v if str(x).strip())
                else:
                    q = str(v).strip() if v is not None else ""
                if q:
                    out["query"] = q
                    break

        # title aliases (LLMs sometimes use "name", "task", "item", "text")
        # NOTE: "content" is intentionally excluded here — it is a distinct required
        # parameter for tools like remember/write_file and has its own alias block below.
        if "title" in params and "title" not in out:
            for alias in ("name", "task", "item", "text", "todo", "description"):
                # Only promote the alias to `title` when the alias is NOT a declared
                # parameter — if it is, the LLM used it intentionally for that param.
                if alias in out and alias not in params:
                    out["title"] = str(out[alias]).strip()
                    break
            # Use "content" as title alias ONLY when this tool has no content param
            if "title" not in out and "content" not in params and "content" in out:
                out["title"] = str(out["content"]).strip()

        # content aliases (remember, write_file, remember_skill, etc.)
        # Fired only when the tool actually declares a `content` parameter.
        if "content" in params and "content" not in out:
            for alias in ("text", "note", "body", "message", "information", "fact", "data"):
                if alias in out and alias not in params:
                    out["content"] = str(out[alias]).strip()
                    break

        # task aliases (LLMs often use message/prompt/instruction/input/text for task delegation)
        if "task" in params and "task" not in out:
            for alias in ("message", "prompt", "instruction", "input", "text", "content", "query", "request"):
                if alias in out and alias not in params:
                    out["task"] = str(out[alias]).strip()
                    break

        # limit aliases
        if "limit" in params and "limit" not in out:
            for alias in ("top_k", "topk", "k", "max_results", "n"):
                if alias in out:
                    try:
                        out["limit"] = int(out[alias])
                    except Exception:
                        pass
                    break

        accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if accepts_varkw:
            return out

        allowed = set(params.keys())
        dropped = [k for k in out.keys() if k not in allowed]
        if dropped:
            log.warning("Tool %s dropping unexpected kwargs: %s", tool_name, ", ".join(dropped))
            out = {k: v for k, v in out.items() if k in allowed}
        return out
