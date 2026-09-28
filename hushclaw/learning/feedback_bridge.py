"""Turn explicit answer ratings into learning signals.

Ratings and disputes used to feed only memory recall. Here a whole-answer
rating or a disputed passage also:
- scores the skills used for that answer (``skill_outcomes``),
- records a reflection for negative feedback, and
- keeps a personal regression case (``eval_cases``).

Only changes are applied, so re-saving the same rating is a no-op. No model
calls happen here.
"""
from __future__ import annotations

import json

from hushclaw.util.logging import get_logger

log = get_logger("learning.feedback")

_NEGATIVE_REASONS = {"缺乏依据", "过于冗长", "偏离问题"}


def _signal(payload: dict, *, quoted: bool) -> str:
    if quoted:
        return "negative" if payload.get("stance") == "disputed" else ""
    rating = int(payload.get("rating") or 0)
    if 1 <= rating <= 2:
        return "negative"
    if rating >= 4:
        return "positive"
    return ""


def _turn_for_answer(memory, assistant_mid: str, session_id: str) -> dict:
    """Find the request and learning trace behind an assistant message."""
    trace: dict = {}
    try:
        row = memory.conn.execute(
            "SELECT payload FROM learning_jobs WHERE json_extract(payload, '$.trace.assistant_message_id')=? LIMIT 1",
            (assistant_mid,),
        ).fetchone()
        if row is not None:
            trace = json.loads(row[0]).get("trace") or {}
    except Exception:
        trace = {}
    user_mid = str(trace.get("source_message_id") or "")
    prompt = str(trace.get("user_input") or "")
    if not prompt:
        answer = memory.resolve_message_ref(assistant_mid, session_id=session_id) or {}
        row = memory.conn.execute(
            "SELECT event_id FROM events WHERE session_id=? AND type='user_message_received' AND ts<=? "
            "ORDER BY ts DESC, rowid DESC LIMIT 1",
            (session_id, int(answer.get("ts") or 0)),
        ).fetchone() if answer else None
        if row is not None:
            user_mid = f"event:{row[0]}"
            source = memory.resolve_message_ref(user_mid, session_id=session_id) or {}
            if not source.get("hidden") and not source.get("excluded"):
                prompt = str(source.get("content") or "")
    return {
        "user_message_id": user_mid,
        "prompt": prompt,
        "task_fingerprint": str(trace.get("task_fingerprint") or ""),
        "used_skills": list(trace.get("used_skills") or []),
    }


def apply_message_feedback(memory, *, message_id: str, session_id: str, before: dict | None, after: dict) -> str:
    """Apply the learning effect of one saved feedback item. Returns the signal applied."""
    items_before = {i["feedback_id"]: i for i in (before or {}).get("items", [])}
    applied = ""
    for item in after.get("items", []):
        if not item.get("active") or item.get("stale"):
            continue
        quoted = bool(item.get("quote"))
        new_signal = _signal(item.get("payload") or {}, quoted=quoted)
        prev = items_before.get(item["feedback_id"])
        old_signal = _signal(prev.get("payload") or {}, quoted=quoted) if prev and prev.get("active") else ""
        if new_signal == old_signal:
            continue
        try:
            _apply(memory, message_id=message_id, session_id=session_id, item=item, signal=new_signal, quoted=quoted)
            applied = new_signal or "cleared"
        except Exception as exc:
            log.warning("feedback learning skipped: %s", exc)
    return applied


def _apply(memory, *, message_id: str, session_id: str, item: dict, signal: str, quoted: bool) -> None:
    eval_cases = getattr(memory, "eval_cases", None)
    evidence_id = item["feedback_id"] if quoted else message_id
    if eval_cases is not None:
        # A rating change replaces the earlier verdict on the same answer.
        for kind in ("negative_feedback", "endorsed"):
            eval_cases.retire(kind=kind, source_message_id=evidence_id)
    if not signal:
        return
    payload = item.get("payload") or {}
    turn = _turn_for_answer(memory, message_id, session_id)
    answer = memory.resolve_message_ref(message_id, session_id=session_id) or {}
    reasons = [r for r in payload.get("reasons") or [] if isinstance(r, str)]
    note = "; ".join(filter(None, [
        f"rating {payload.get('rating')}/5" if payload.get("rating") else "",
        f"disputed: {item.get('quote', '')[:200]}" if quoted else "",
        "、".join(reasons),
        str(payload.get("reason") or ""),
    ]))

    rating = int(payload.get("rating") or 0)
    quality = 0.0 if quoted else (1.0 if signal == "positive" else max(0.0, (rating - 1) / 4))
    for skill in turn["used_skills"]:
        memory.record_skill_outcome(
            skill_name=skill,
            session_id=session_id,
            task_fingerprint=turn["task_fingerprint"],
            success=signal == "positive",
            note=f"User feedback: {note}"[:400],
            quality_score=quality,
        )

    if signal == "negative":
        memory.record_reflection(
            session_id=session_id,
            task_fingerprint=turn["task_fingerprint"] or "general_assistance",
            success=False,
            outcome="User rated the answer negatively.",
            failure_mode=note[:200],
            lesson=str(payload.get("reason") or "、".join(reasons) or "")[:280],
            skill_name=(turn["used_skills"][0] if turn["used_skills"] else ""),
            source_message_id=message_id,
        )

    if eval_cases is None or not turn["prompt"]:
        return
    if signal == "negative":
        expectation = "Answer the request without the problems the user flagged: " + (note or "low rating")
        if quoted:
            expectation += f"\nDo not repeat the disputed claim: {item.get('quote', '')[:400]}"
        kind = "negative_feedback"
    else:
        positives = [r for r in reasons if r not in _NEGATIVE_REASONS]
        expectation = "Keep the qualities the user rated highly" + (f": {'、'.join(positives)}" if positives else ".")
        kind = "endorsed"
    eval_cases.upsert(
        kind=kind,
        session_id=session_id,
        source_message_id=evidence_id,
        prompt_message_id=turn["user_message_id"],
        response_message_id=message_id,
        prompt=turn["prompt"],
        response=str(answer.get("content") or ""),
        expectation=expectation,
        task_fingerprint=turn["task_fingerprint"],
        skills=turn["used_skills"],
    )
