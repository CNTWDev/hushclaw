"""Runtime wrapper around tool execution."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from hushclaw.runtime.policy import PolicyDecision, PolicyGate
from hushclaw.tools.base import ToolResult
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.runtime_context import ToolRuntimeContext


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""
    entrypoint: str = ""
    workspace: str = ""


@dataclass(slots=True)
class ToolExecutionRecord:
    call: ToolCall
    result: ToolResult
    decision: PolicyDecision
    elapsed_ms: float


class ToolRuntime:
    """Apply runtime policy checks before delegating to ToolExecutor."""

    def __init__(
        self,
        executor: ToolExecutor,
        policy_gate: PolicyGate,
        runtime_context: ToolRuntimeContext,
    ) -> None:
        self.executor = executor
        self.policy_gate = policy_gate
        self.runtime_context = runtime_context
        self.executor.set_runtime_context(runtime_context)

    def set_context(self, **kwargs: Any) -> None:
        """Keep legacy context mutation working while centralizing storage."""
        self.executor.set_context(**kwargs)

    async def execute(self, call: ToolCall) -> ToolExecutionRecord:
        td = self.executor.registry.get(call.name)
        if td is None:
            result = ToolResult.error(f"Unknown tool: {call.name!r}")
            decision = PolicyDecision(allowed=False, reason=result.content)
            return ToolExecutionRecord(call=call, result=result, decision=decision, elapsed_ms=0.0)

        decision = self.policy_gate.check(td, call.arguments, self.runtime_context)
        if not decision.allowed:
            return ToolExecutionRecord(
                call=call,
                result=ToolResult.error(decision.reason or f"Blocked by runtime policy for tool {call.name!r}"),
                decision=decision,
                elapsed_ms=0.0,
            )

        started = time.monotonic()
        result = await self.executor.execute(call.name, call.arguments)
        elapsed_ms = (time.monotonic() - started) * 1000
        return ToolExecutionRecord(call=call, result=result, decision=decision, elapsed_ms=elapsed_ms)
