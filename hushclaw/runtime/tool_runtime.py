"""Runtime wrapper around tool execution."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from hushclaw.runtime.policy import (
    DEFAULT_APPROVAL_REQUIRED,
    DEFAULT_UNATTENDED_CHANNELS,
    PolicyDecision,
    PolicyGate,
    approval_summary,
)
from hushclaw.runtime.audit import AuditEvent, append_audit_event
from hushclaw.runtime.principal import current_principal
from hushclaw.runtime.file_verifier import candidate_paths, should_verify_tool, snapshot, verify_mutation
from hushclaw.tools.base import ToolResult
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.runtime_context import ToolRuntimeContext

# Legacy tool names that have been collapsed into a single public facade.
# Any provider or skill pack that still emits these names is silently remapped.
_TOOL_ALIASES: dict[str, str] = {
    "patch_document": "edit_document",
    "update_document": "edit_document",
}


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

    def _approval_settings(self) -> tuple[set[str], set[str]]:
        tools_cfg = getattr(getattr(self.runtime_context, "config", None), "tools", None)
        required = getattr(tools_cfg, "approval_required", None)
        allowed = getattr(tools_cfg, "unattended_allow_channels", None)
        return (
            set(DEFAULT_APPROVAL_REQUIRED if required is None else required),
            set(DEFAULT_UNATTENDED_CHANNELS if allowed is None else allowed),
        )

    async def _check_approval(self, td, tool_name: str, arguments: dict[str, Any]) -> str:
        """Return a denial reason, or "" when the call may proceed.

        Order: the REPL's synchronous confirm prompt, then the WebUI session's
        approval dialog, then the unattended channel allowlist.
        """
        required, unattended = self._approval_settings()
        if tool_name not in required:
            return ""
        summary = approval_summary(tool_name, arguments)
        confirm_fn = self.runtime_context.get("_confirm_fn")
        if callable(confirm_fn):
            if tool_name == "run_shell":
                return ""  # PolicyGate.check already prompted for this command
            return "" if confirm_fn(summary) else "Cancelled by user."
        entry = self.runtime_context.get("_current_session_entry")
        request_approval = getattr(entry, "request_approval", None)
        if callable(request_approval):
            try:
                approved = await request_approval(tool_name, dict(arguments or {}), summary=summary)
            except Exception as exc:
                return f"Approval request failed: {exc}"
            return "" if approved else (
                f"The user did not approve {tool_name}. Do not retry it; ask the user how to proceed."
            )
        # Prefer the principal of the current run (set per request by callers
        # such as the scheduler or inbound automation); loops are cached per
        # session, so the principal captured at loop creation can be stale.
        run_channel = str(getattr(current_principal(), "source_channel", "") or "")
        if run_channel and run_channel != "local":
            channel = run_channel
        else:
            principal = self.runtime_context.effective_principal()
            channel = str(
                getattr(principal, "source_channel", "")
                or getattr(self.runtime_context, "source_channel", "")
                or "local"
            )
        if channel in unattended:
            return ""
        return (
            f"{tool_name} requires user approval and channel {channel!r} has no interactive "
            "approver. The owner can allow it via tools.unattended_allow_channels."
        )

    async def execute(self, call: ToolCall) -> ToolExecutionRecord:
        resolved_name = _TOOL_ALIASES.get(call.name, call.name)
        td = self.executor.registry.get(resolved_name)
        principal = self.runtime_context.effective_principal()
        memory = getattr(self.runtime_context, "memory", None)
        session_id = self.runtime_context.session_id
        if td is None:
            result = ToolResult.error(f"Unknown tool: {call.name!r}")
            decision = PolicyDecision(allowed=False, reason=result.content)
            append_audit_event(memory, AuditEvent(
                event_type="policy_denied",
                principal=principal,
                session_id=session_id,
                resource={"kind": "tool", "id": call.name},
                metadata={"reason": result.content, "entrypoint": call.entrypoint},
            ))
            return ToolExecutionRecord(call=call, result=result, decision=decision, elapsed_ms=0.0)

        allowed_tools = getattr(
            getattr(self.runtime_context.config, "agent", None), "allowed_tools", None
        )
        if allowed_tools is not None and resolved_name not in allowed_tools:
            result = ToolResult.error(f"Tool {call.name!r} not permitted by session policy")
            decision = PolicyDecision(allowed=False, reason=result.content)
            append_audit_event(memory, AuditEvent(
                event_type="policy_denied",
                principal=principal,
                session_id=session_id,
                resource={"kind": "tool", "id": call.name},
                metadata={"reason": "tool_acl", "entrypoint": call.entrypoint},
            ))
            return ToolExecutionRecord(call=call, result=result, decision=decision, elapsed_ms=0.0)

        decision = self.policy_gate.check(td, call.arguments, self.runtime_context)
        if not decision.allowed:
            append_audit_event(memory, AuditEvent(
                event_type="policy_denied",
                principal=principal,
                session_id=session_id,
                resource={"kind": "tool", "id": call.name, "arguments": call.arguments},
                approval_state="denied" if decision.requires_confirmation else "none",
                metadata={"reason": decision.reason, "entrypoint": call.entrypoint},
            ))
            return ToolExecutionRecord(
                call=call,
                result=ToolResult.error(decision.reason or f"Blocked by runtime policy for tool {call.name!r}"),
                decision=decision,
                elapsed_ms=0.0,
            )

        approval_denial = await self._check_approval(td, resolved_name, call.arguments)
        if approval_denial:
            decision = PolicyDecision(allowed=False, reason=approval_denial, requires_confirmation=True)
            append_audit_event(memory, AuditEvent(
                event_type="policy_denied",
                principal=principal,
                session_id=session_id,
                resource={"kind": "tool", "id": call.name, "arguments": call.arguments},
                approval_state="denied",
                metadata={"reason": approval_denial, "entrypoint": call.entrypoint},
            ))
            return ToolExecutionRecord(
                call=call,
                result=ToolResult.error(approval_denial),
                decision=decision,
                elapsed_ms=0.0,
            )

        append_audit_event(memory, AuditEvent(
            event_type="tool_call",
            principal=principal,
            session_id=session_id,
            resource={"kind": "tool", "id": call.name, "arguments": call.arguments},
            metadata={"entrypoint": call.entrypoint, "workspace": call.workspace, **decision.annotations},
        ))
        workspace_dir = getattr(getattr(self.runtime_context.config, "agent", None), "workspace_dir", None)
        before_snapshots = {}
        if should_verify_tool(resolved_name):
            for path in candidate_paths(resolved_name, call.arguments, workspace_dir=workspace_dir):
                before_snapshots[str(path)] = snapshot(path)
        started = time.monotonic()
        result = await self.executor.execute(call.name, call.arguments)
        elapsed_ms = (time.monotonic() - started) * 1000
        mutation_summary = None
        if should_verify_tool(resolved_name):
            mutation_summary = verify_mutation(
                resolved_name,
                call.arguments,
                workspace_dir=workspace_dir,
                before=before_snapshots,
            )
            if mutation_summary is not None:
                metadata = dict(result.metadata or {})
                metadata["mutation_summary"] = mutation_summary.to_dict()
                result.metadata = metadata
                missing_files = [
                    item["path"] for item in mutation_summary.files
                    if not item.get("exists")
                ]
                invalid_files = [
                    item["path"] for item in mutation_summary.diagnostics
                    if not item.get("ok")
                ]
                if not result.is_error and (missing_files or invalid_files):
                    reasons = []
                    if missing_files:
                        reasons.append("missing: " + ", ".join(missing_files))
                    if invalid_files:
                        reasons.append("invalid: " + ", ".join(invalid_files))
                    result = ToolResult(
                        content=f"{result.content}\nVerification failed ({'; '.join(reasons)}).",
                        is_error=True,
                        artifact_id=result.artifact_id,
                        metadata=metadata,
                    )
        append_audit_event(memory, AuditEvent(
            event_type="tool_result",
            principal=principal,
            session_id=session_id,
            resource={"kind": "tool", "id": call.name},
            metadata={
                "entrypoint": call.entrypoint,
                "workspace": call.workspace,
                "elapsed_ms": elapsed_ms,
                "is_error": result.is_error,
                "artifact_id": result.artifact_id,
                "result_metadata": result.metadata or {},
                "mutation_summary": mutation_summary.to_dict() if mutation_summary is not None else None,
            },
        ), status="failed" if result.is_error else "completed")
        return ToolExecutionRecord(call=call, result=result, decision=decision, elapsed_ms=elapsed_ms)
