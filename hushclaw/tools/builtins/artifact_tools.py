"""Artifact tools for reading runtime-stored tool outputs."""
from __future__ import annotations

from hushclaw.tools.base import ToolResult, tool

_MAX_ARTIFACT_READ_CHARS = 16_000


@tool(
    name="read_artifact",
    description=(
        "Read text content previously stored as an artifact by tool output budgeting. "
        "Use this with the artifact_id shown in a tool result when the preview is insufficient."
    ),
)
def read_artifact(
    artifact_id: str,
    max_chars: int = _MAX_ARTIFACT_READ_CHARS,
    _memory_store=None,
) -> ToolResult:
    artifact_id = (artifact_id or "").strip().strip("`")
    if not artifact_id:
        return ToolResult.error("artifact_id is required")
    if _memory_store is None or not hasattr(_memory_store, "artifacts"):
        return ToolResult.error("Artifact store is not available.")

    metadata = _memory_store.artifacts.metadata(artifact_id)
    if metadata is None:
        return ToolResult.error(f"Artifact not found: {artifact_id}")
    raw = _memory_store.artifacts.load(artifact_id)
    if raw is None:
        return ToolResult.error(f"Artifact content is missing: {artifact_id}")

    text = raw.decode("utf-8", errors="replace")
    limit = max(1, min(int(max_chars or _MAX_ARTIFACT_READ_CHARS), _MAX_ARTIFACT_READ_CHARS))
    if len(text) > limit:
        text = (
            text[:limit].rstrip()
            + f"\n\n[read_artifact truncated output at {limit:,} chars; "
            "call again with a narrower downstream request if needed.]"
        )
    return ToolResult(
        content=text,
        metadata={
            "artifact_id": artifact_id,
            "size_bytes": int(metadata.get("size_bytes") or 0),
            "tool_name": metadata.get("tool_name", ""),
            "mime_type": metadata.get("mime_type", ""),
        },
    )
