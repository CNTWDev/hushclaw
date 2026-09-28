"""Turn change digest: what one assistant turn changed for the user."""
from __future__ import annotations

import json

from hushclaw.runtime.threat_patterns import wrap_untrusted_context
from hushclaw.runtime.turn_changes import TurnChangeLog, describe_changes
from hushclaw.tools.base import ToolResult
from hushclaw.tools.builtins.memory_tools import remember


class _Mem:
    def remember(self, content, *, scope="global", principal=None, metadata=None):
        return "note-1234567890"


def _wrapped(result: ToolResult, tool: str) -> ToolResult:
    """Mimic the executor: content wrapped as untrusted, metadata preserved."""
    content, _scan = wrap_untrusted_context(result.content, source=f"tool:{tool}", kind="tool_result")
    return ToolResult(content=content, is_error=result.is_error,
                      artifact_id=result.artifact_id, metadata=result.metadata)


def test_remember_declares_memory_change_with_full_note_id():
    out = remember(content="The user prefers concise answers", title="Concise", _memory_port=_Mem())
    changes = describe_changes("remember", {}, _wrapped(out, "remember"))
    assert changes == [{
        "kind": "memory",
        "action": "created",
        "title": "Concise",
        "detail": "The user prefers concise answers",
        "ref": {"note_id": "note-1234567890", "note_type": "fact", "scope": "global"},
    }]


def test_errors_produce_no_changes():
    assert describe_changes("remember", {}, ToolResult.error("boom")) == []
    assert describe_changes("write_file", {"path": "a.md"}, ToolResult.error("denied")) == []


def test_write_file_through_discovery_bridge():
    result = ToolResult.ok("Written 10 chars to /tmp/x/plan.md")
    result.artifact_id = "abc123"
    changes = describe_changes(
        "tool_call", {"name": "write_file", "arguments": {"path": "plan.md", "content": "x"}}, result,
    )
    assert len(changes) == 1
    assert changes[0]["kind"] == "file"
    assert changes[0]["title"] == "plan.md"
    assert changes[0]["ref"] == {"path": "plan.md", "url": "/files/abc123"}


def test_edit_document_reads_wrapped_json_payload():
    payload = {"path": "/w/notes.md", "url": "/files/f1", "change_summary": "Tightened intro"}
    result = _wrapped(ToolResult.ok(json.dumps(payload)), "edit_document")
    [change] = describe_changes("edit_document", {"path": "notes.md"}, result)
    assert change["action"] == "updated"
    assert change["detail"] == "Tightened intro"
    assert change["ref"] == {"path": "/w/notes.md", "url": "/files/f1"}


def test_untracked_tools_are_ignored():
    assert describe_changes("read_file", {"path": "a.md"}, ToolResult.ok("hello")) == []


def test_log_dedupes_repeated_edits_and_keeps_first_action():
    log = TurnChangeLog()
    created = ToolResult.ok("ok")
    created.artifact_id = "f1"
    log.record("write_file", {"path": "a.md"}, created)
    log.record("apply_patch", {"operations": [
        {"path": "a.md", "old": "x", "new": "y"},
        {"path": "b.md", "old": "x", "new": "y"},
        {"path": "a.md", "old": "y", "new": "z"},
    ]}, ToolResult.ok("ok"))
    snap = log.snapshot()
    assert [(c["title"], c["action"]) for c in snap] == [("a.md", "created"), ("b.md", "updated")]
    assert snap[0]["ref"]["url"] == "/files/f1"


def test_log_never_raises_on_odd_input():
    log = TurnChangeLog()
    log.record("apply_patch", {"operations": "not-a-list"}, ToolResult.ok("ok"))
    log.record("tool_call", None, ToolResult.ok("ok"))
    assert log.snapshot() == []
