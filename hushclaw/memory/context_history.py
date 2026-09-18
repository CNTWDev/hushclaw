"""Bounded context checkpoints over durable conversation history.

A summary replaces only the prefix before a stable user-message boundary.
Edits, exclusions or missing boundaries invalidate it; raw history remains the
source of truth. Checkpoints live inside the database, not plaintext files.
"""
from __future__ import annotations

import hashlib
import json

from hushclaw.prompts import COMPACT_SUMMARY_PREFIX
from hushclaw.providers.base import Message


def fingerprint(messages: list[Message]) -> str:
    payload = [(m.source_id, m.role, m.content, m.tool_call_id, m.tool_name) for m in messages]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def raw_history(memory, session_id: str, thread_id: str = "") -> list[Message]:
    messages = memory.session_log.replay_context(session_id=session_id, thread_id=thread_id)
    if messages:
        return _merge_legacy_turns(memory, session_id, thread_id, messages)
    if thread_id:
        row = memory.conn.execute(
            "SELECT parent_thread_id FROM threads WHERE thread_id=? AND session_id=?",
            (thread_id, session_id),
        ).fetchone()
        # Never import a parent's transcript into an empty child thread.
        if row is None or row["parent_thread_id"]:
            return []
    # Existing (possibly excluded) events mean this is not legacy history.
    # Never resurrect excluded event messages via their turn-store mirrors.
    scope = "thread_id" if thread_id else "session_id"
    if memory.conn.execute(f"SELECT 1 FROM events WHERE {scope}=? AND type IN "
                           "('user_message_received','assistant_message_emitted') LIMIT 1",
                           (thread_id or session_id,)).fetchone():
        return []
    return [Message(role=t["role"], content=t["content"], source_id=t["message_id"])
            for t in memory._apply_message_states(memory.load_session_turns(session_id), include_hidden=False)
            if t["role"] in {"user", "assistant"} and not t.get("excluded")]


def _merge_legacy_turns(memory, session_id: str, thread_id: str, messages: list[Message]) -> list[Message]:
    """Keep pre-event history and gaps after best-effort event writes fail.

    Only merge where durable turn IDs prove the stores belong to the same
    primary conversation. Never guess by matching text or mix child threads.
    """
    roots = memory.conn.execute(
        "SELECT thread_id FROM threads WHERE session_id=? AND parent_thread_id=''", (session_id,),
    ).fetchall()
    if thread_id and (len(roots) != 1 or roots[0]["thread_id"] != thread_id):
        return messages
    rows = memory.conn.execute(
        "SELECT event_id,type,payload_json FROM events WHERE session_id=? AND type IN "
        "('user_message_received','assistant_message_emitted')", (session_id,),
    ).fetchall()
    linked = {}
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        key = "user_turn_id" if row["type"] == "user_message_received" else "assistant_turn_id"
        if payload.get(key):
            linked["turn:" + payload[key]] = "event:" + row["event_id"]
    if not linked:
        return messages
    turns = memory._apply_message_states(memory.load_session_turns(session_id), include_hidden=False)
    indices = {m.source_id: i for i, m in enumerate(messages)}
    anchors = [(i, indices[linked[t["message_id"]]]) for i, t in enumerate(turns)
               if t["message_id"] in linked and linked[t["message_id"]] in indices]
    if not anchors:
        return messages
    ordered = [(float(i), m) for i, m in enumerate(messages)]
    for i, turn in enumerate(turns):
        if turn["message_id"] in linked or turn["role"] not in {"user", "assistant"} or turn.get("excluded"):
            continue
        before = next((a for a in reversed(anchors) if a[0] < i), None)
        after = next((a for a in anchors if a[0] > i), None)
        if before and after:
            if before[1] >= after[1]:
                continue  # ambiguous ordering: do not invent a transcript
            position = before[1] + (after[1] - before[1]) * (i - before[0]) / (after[0] - before[0])
        elif after:
            position = after[1] - (after[0] - i) / (len(turns) + 1)
        else:
            position = len(messages) + i / (len(turns) + 1)
        ordered.append((position, Message(role=turn["role"], content=turn["content"], source_id=turn["message_id"])))
    return [message for _, message in sorted(ordered, key=lambda item: item[0])]


def restore_history(memory, session_id: str, thread_id: str = "") -> list[Message]:
    messages = raw_history(memory, session_id, thread_id)
    checkpoints = memory.conn.execute(
        "SELECT * FROM context_checkpoints WHERE session_id=?" + (" AND thread_id=?" if thread_id else ""),
        (session_id, thread_id) if thread_id else (session_id,),
    ).fetchall()
    best = None
    for checkpoint in checkpoints:
        for index, message in enumerate(messages):
            if message.source_id == checkpoint["resume_message_id"]:
                if fingerprint(messages[:index]) == checkpoint["prefix_hash"]:
                    if best is None or index > best[0]:
                        best = (index, checkpoint["summary"])
                break
    if best:
        return [Message(role="user", content=COMPACT_SUMMARY_PREFIX + "\n" + best[1],
                        context_kind="summary")] + messages[best[0]:]
    # Legacy summary.md has no coverage boundary. It must never replace raw
    # messages or be prepended as an authority that could contradict corrections.
    return messages


def prepare_checkpoint(memory, session_id: str, resume_message_id: str) -> dict | None:
    if not resume_message_id:
        return None
    thread_id = ""
    if resume_message_id.startswith("event:"):
        row = memory.conn.execute("SELECT thread_id FROM events WHERE event_id=? AND session_id=?",
                                  (resume_message_id[6:], session_id)).fetchone()
        if row is None:
            return None
        thread_id = row["thread_id"] or ""
    messages = raw_history(memory, session_id, thread_id)
    for index, message in enumerate(messages):
        if message.source_id == resume_message_id and message.role == "user" and index:
            return {"session_id": session_id, "thread_id": thread_id,
                    "resume_message_id": resume_message_id, "prefix_hash": fingerprint(messages[:index])}
    return None


def save_checkpoint(memory, checkpoint: dict, summary: str) -> bool:
    # Do not commit stale coverage if history changed during the summary call.
    current = prepare_checkpoint(memory, checkpoint["session_id"], checkpoint["resume_message_id"])
    if current != checkpoint:
        return False
    memory.conn.execute(
        "INSERT INTO context_checkpoints(session_id,thread_id,summary,resume_message_id,prefix_hash) "
        "VALUES(?,?,?,?,?) ON CONFLICT(session_id,thread_id) DO UPDATE SET "
        "summary=excluded.summary,resume_message_id=excluded.resume_message_id,prefix_hash=excluded.prefix_hash",
        (checkpoint["session_id"], checkpoint["thread_id"], summary,
         checkpoint["resume_message_id"], checkpoint["prefix_hash"]),
    )
    memory.conn.commit()
    return True
