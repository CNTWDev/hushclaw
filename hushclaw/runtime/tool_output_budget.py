"""Tool output budgeting and artifact offload helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hushclaw.tools.base import ToolResult


@dataclass(frozen=True, slots=True)
class ToolOutputBudget:
    """Controls how much tool output is allowed back into model context."""

    inline_char_limit: int = 12_000
    preview_char_limit: int = 1_200
    summary_char_limit: int = 500


DEFAULT_TOOL_OUTPUT_BUDGET = ToolOutputBudget()


def _single_line(text: str, limit: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(0, int(limit * 0.7))
    tail = max(0, limit - head)
    return (
        text[:head].rstrip()
        + "\n\n...[middle omitted for tool output budget]...\n\n"
        + text[-tail:].lstrip()
    )


def _artifact_store(memory: Any) -> Any:
    return getattr(memory, "artifacts", None) if memory is not None else None


def apply_tool_output_budget(
    result: ToolResult,
    *,
    tool_name: str,
    memory: Any,
    session_id: str,
    budget: ToolOutputBudget = DEFAULT_TOOL_OUTPUT_BUDGET,
) -> ToolResult:
    """Return a context-sized result, offloading large output when possible."""

    if result.is_error or result.artifact_id:
        return result
    content = result.content or ""
    if len(content) <= budget.inline_char_limit:
        return result

    artifacts = _artifact_store(memory)
    if artifacts is None:
        preview = _preview(content, budget.preview_char_limit)
        return ToolResult(
            content=(
                f"{preview}\n\n"
                f"[Tool output budget: original result was {len(content):,} chars, "
                "but no artifact store is available. Ask the tool for a narrower result.]"
            ),
            is_error=False,
            metadata={
                **(result.metadata or {}),
                "budgeted": True,
                "original_chars": len(content),
                "preview_chars": len(preview),
                "artifact_offloaded": False,
            },
        )

    summary = _single_line(content, budget.summary_char_limit)
    artifact_id = artifacts.save(
        session_id,
        content,
        tool_name=tool_name,
        mime_type="text/plain",
        summary=summary,
    )
    preview = _preview(content, budget.preview_char_limit)
    return ToolResult(
        content=(
            f"{preview}\n\n"
            f"[Tool output budget: original result was {len(content):,} chars. "
            f"Full content stored as artifact `{artifact_id}`. "
            f"Call read_artifact(artifact_id=\"{artifact_id}\") if the full output is needed later.]"
        ),
        is_error=False,
        artifact_id=artifact_id,
        metadata={
            **(result.metadata or {}),
            "budgeted": True,
            "original_chars": len(content),
            "preview_chars": len(preview),
            "artifact_offloaded": True,
        },
    )
