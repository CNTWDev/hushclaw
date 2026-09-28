"""Personal regression cases distilled from corrections and explicit ratings.

Every time the user corrects an answer or rates one, the exchange becomes a
case: the original request, the answer that was judged, and what a good answer
must do. ``hushclaw eval run`` replays active cases so a change to skills,
prompts or memory policy can be checked against the user's own history.

Cases hold user text, so they follow the same deletion rules as other derived
data: purging either referenced message or the session removes them.
"""
from __future__ import annotations

import hashlib
import json
import time

KINDS = ("correction", "negative_feedback", "endorsed")


def _case_id(kind: str, source_message_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_message_id}".encode()).hexdigest()[:24]
    return f"eval-{digest}"


class EvalCaseStore:
    def __init__(self, memory):
        self.memory, self.conn = memory, memory.conn

    def upsert(
        self,
        *,
        kind: str,
        session_id: str,
        source_message_id: str,
        prompt: str,
        response: str = "",
        expectation: str = "",
        prompt_message_id: str = "",
        response_message_id: str = "",
        task_fingerprint: str = "",
        skills: list[str] | None = None,
    ) -> str:
        """Create or refresh the case for one piece of evidence. Idempotent."""
        if kind not in KINDS:
            raise ValueError(f"unknown eval case kind: {kind!r}")
        prompt = str(prompt or "").strip()
        if not prompt or not source_message_id:
            return ""
        case_id = _case_id(kind, source_message_id)
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO eval_cases(case_id, kind, session_id, source_message_id, prompt_message_id,
                   response_message_id, task_fingerprint, prompt, response, expectation, skills_json,
                   status, created, updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
               ON CONFLICT(case_id) DO UPDATE SET
                   prompt=excluded.prompt, response=excluded.response, expectation=excluded.expectation,
                   task_fingerprint=excluded.task_fingerprint, skills_json=excluded.skills_json,
                   status='active', updated=excluded.updated""",
            (
                case_id, kind, session_id, source_message_id, prompt_message_id or "",
                response_message_id or "", task_fingerprint or "", prompt[:4000], str(response or "")[:6000],
                str(expectation or "").strip()[:2000], json.dumps(list(skills or []), ensure_ascii=False),
                now, now,
            ),
        )
        self.conn.commit()
        return case_id

    def retire(self, *, kind: str, source_message_id: str) -> bool:
        changed = self.conn.execute(
            "UPDATE eval_cases SET status='retired', updated=? WHERE case_id=? AND status='active'",
            (int(time.time()), _case_id(kind, source_message_id)),
        ).rowcount
        self.conn.commit()
        return bool(changed)

    def list(self, *, kind: str = "", status: str = "active", limit: int = 100) -> list[dict]:
        clauses, params = [], []
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM eval_cases {where} ORDER BY created DESC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["skills"] = json.loads(item.pop("skills_json") or "[]")
            out.append(item)
        return out

    def record_run(self, case_id: str, *, passed: bool, reason: str = "", response: str = "", batch_id: str = "") -> None:
        self.conn.execute(
            "INSERT INTO eval_runs(case_id, batch_id, passed, reason, response, created) VALUES (?,?,?,?,?,?)",
            (case_id, batch_id, 1 if passed else 0, str(reason or "")[:1000], str(response or "")[:6000], int(time.time())),
        )
        self.conn.commit()

    def last_runs(self, case_id: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM eval_runs WHERE case_id=? ORDER BY created DESC, run_id DESC LIMIT ?",
            (case_id, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, *, mid: str = "", sid: str = "") -> int:
        if mid:
            where = "source_message_id=? OR prompt_message_id=? OR response_message_id=?"
            params = (mid, mid, mid)
        elif sid:
            where, params = "session_id=?", (sid,)
        else:
            return 0
        self.conn.execute(
            f"DELETE FROM eval_runs WHERE case_id IN (SELECT case_id FROM eval_cases WHERE {where})", params
        )
        removed = self.conn.execute(f"DELETE FROM eval_cases WHERE {where}", params).rowcount
        return int(removed or 0)
