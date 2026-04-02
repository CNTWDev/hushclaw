"""Async tool executor with timeout and error isolation."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

from hushclaw.tools.base import ToolDefinition, ToolResult
from hushclaw.util.logging import get_logger

log = get_logger("tools.executor")


class ToolExecutor:
    def __init__(self, registry, timeout: int = 30) -> None:
        self.registry = registry
        self.timeout = timeout
        self._context: dict[str, Any] = {}

    def set_context(self, **kwargs: Any) -> None:
        """Inject context objects (e.g. memory_store, config) for tools."""
        self._context.update(kwargs)

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Return a context value by key (public accessor, avoids private _context access)."""
        return self._context.get(key, default)

    async def execute_single(self, name: str, arguments: dict) -> ToolResult:
        """Execute a single tool call, identical to :meth:`execute` but intended
        for direct (non-LLM-driven) invocations such as REPL ``direct_tool`` dispatch."""
        return await self.execute(name, arguments)

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        td: ToolDefinition | None = self.registry.get(name)
        if td is None:
            return ToolResult.error(f"Unknown tool: {name!r}")

        # Inject context variables that the function accepts
        sig = inspect.signature(td.fn)
        kwargs = dict(arguments)
        for ctx_key, ctx_val in self._context.items():
            if ctx_key in sig.parameters:
                kwargs[ctx_key] = ctx_val

        # Per-tool timeout overrides the global executor timeout.
        # timeout=0 means no timeout (used for tools that await sub-agent LLM calls).
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
            log.error("Tool %s raised: %s", name, e, exc_info=True)
            return ToolResult.error(f"Tool {name!r} error: {e}")

        if isinstance(result, ToolResult):
            return result
        return ToolResult.ok(result)
