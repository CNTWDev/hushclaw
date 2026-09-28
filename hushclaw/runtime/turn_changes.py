"""Turn change digest — what a single assistant turn changed for the user.

Collected from tool results while the turn runs and attached to the ``done``
event (and the persisted ``assistant_message_emitted`` payload) so the Web UI
can show one reviewable card per turn: memories saved, skills evolved, files
written.  Tools may declare a change explicitly via
``ToolResult.metadata["change"]``; well-known file tools are inferred here so
tool modules stay framework-agnostic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hushclaw.runtime.threat_patterns import unwrap_untrusted_context

MAX_CHANGES_PER_TURN = 50
_TEXT_LIMIT = 160

_FILE_TOOLS = {"write_file", "edit_document", "apply_patch"}


def _clip(text: Any, limit: int = _TEXT_LIMIT) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _unwrap_bridge(tool_name: str, tool_input: Any) -> tuple[str, dict]:
    """Resolve the progressive-discovery ``tool_call`` bridge to the real tool."""
    args = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "tool_call":
        inner = args.get("arguments")
        return str(args.get("name") or ""), inner if isinstance(inner, dict) else {}
    return tool_name, args


def _file_change(path: str, *, action: str, url: str = "", detail: str = "") -> dict:
    return {
        "kind": "file",
        "action": action,
        "title": Path(path).name or path,
        "detail": _clip(detail or path),
        "ref": {"path": path, "url": url},
    }


def _infer_file_changes(tool_name: str, args: dict, result) -> list[dict]:
    content = unwrap_untrusted_context(str(getattr(result, "content", "") or ""))
    if tool_name == "write_file":
        path = str(args.get("path") or "")
        file_id = str(getattr(result, "artifact_id", "") or "")
        return [_file_change(path, action="created", url=f"/files/{file_id}" if file_id else "")] if path else []
    if tool_name == "edit_document":
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            payload = {}
        path = str(payload.get("path") or args.get("path") or "")
        if not path:
            return []
        return [_file_change(
            path,
            action="updated",
            url=str(payload.get("url") or ""),
            detail=str(payload.get("change_summary") or "") or path,
        )]
    if tool_name == "apply_patch":
        seen: list[str] = []
        for op in args.get("operations") or []:
            path = str(op.get("path") or "") if isinstance(op, dict) else ""
            if path and path not in seen:
                seen.append(path)
        return [_file_change(p, action="updated") for p in seen]
    return []


def describe_changes(tool_name: str, tool_input: Any, result) -> list[dict]:
    """Return the user-visible changes one tool result made (possibly none)."""
    if result is None or getattr(result, "is_error", False):
        return []
    metadata = getattr(result, "metadata", None)
    declared = metadata.get("change") if isinstance(metadata, dict) else None
    if isinstance(declared, dict) and declared.get("kind"):
        change = dict(declared)
        change["title"] = _clip(change.get("title"), 80)
        change["detail"] = _clip(change.get("detail"))
        change.setdefault("action", "created")
        change.setdefault("ref", {})
        return [change]
    name, args = _unwrap_bridge(tool_name, tool_input)
    if name in _FILE_TOOLS:
        return _infer_file_changes(name, args, result)
    return []


class TurnChangeLog:
    """Per-turn accumulator; deduplicates repeated edits to the same target."""

    def __init__(self) -> None:
        self._items: list[dict] = []

    def reset(self) -> None:
        self._items = []

    def record(self, tool_name: str, tool_input: Any, result) -> None:
        try:
            changes = describe_changes(tool_name, tool_input, result)
        except Exception:
            return  # a digest must never break a turn
        for change in changes:
            key = self._key(change)
            existing = next((c for c in self._items if self._key(c) == key), None) if key else None
            if existing is not None:
                # Keep the first action ("created" stays created) but refresh details.
                existing["detail"] = change.get("detail") or existing.get("detail", "")
                if change.get("ref", {}).get("url"):
                    existing.setdefault("ref", {})["url"] = change["ref"]["url"]
                continue
            if len(self._items) < MAX_CHANGES_PER_TURN:
                self._items.append(change)

    @staticmethod
    def _key(change: dict) -> str:
        ref = change.get("ref") or {}
        target = ref.get("path") or ref.get("skill") or ref.get("note_id") or ""
        return f"{change.get('kind')}:{target}" if target else ""

    def snapshot(self) -> list[dict]:
        return [dict(c, ref=dict(c.get("ref") or {})) for c in self._items]
