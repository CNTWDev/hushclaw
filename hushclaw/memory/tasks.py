"""Reliable work-task lifecycle storage.

This module owns the small amount of coordination state required by background
agent work: dependency gates, atomic claims, renewable leases, evidence, and a
deterministic completion contract.  ``MemoryStore`` remains the compatibility
facade; task lifecycle rules live here so they cannot drift across callers.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from hushclaw.memory.events import _conn_lock
from hushclaw.util.ids import make_id

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_STALE = "stale"
TASK_STATUS_DONE = "done"
TASK_CLAIMABLE_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_BLOCKED, TASK_STATUS_STALE}

TASK_RUN_STATUS_RUNNING = "running"
TASK_RUN_STATUS_SUCCEEDED = "succeeded"
TASK_RUN_STATUS_FAILED = "failed"
TASK_RUN_STATUS_STALE = "stale"

PROOF_RESPONSE = "response"
PROOF_TOOL = "tool"
PROOF_ARTIFACT = "artifact"
PROOF_NONE = "none"
PROOF_KINDS = {PROOF_RESPONSE, PROOF_TOOL, PROOF_ARTIFACT, PROOF_NONE}


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(str(raw or "[]"))
        return value if isinstance(value, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _json_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


class TaskRunStore:
    """SQLite-backed task state machine with compatibility-friendly dict I/O."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_task(
        self,
        title: str,
        spec: str = "",
        *,
        parent_task_id: str = "",
        dependencies: list[str] | None = None,
        workspace: str = "",
        model_override: str = "",
        metadata: dict | None = None,
        acceptance_criteria: list[str] | None = None,
        proof_required: str = PROOF_RESPONSE,
        status: str = TASK_STATUS_QUEUED,
    ) -> dict:
        task_id = "task-" + make_id()
        now = int(time.time())
        meta = dict(metadata or {})
        existing_contract = meta.get("completion_contract")
        contract = dict(existing_contract) if isinstance(existing_contract, dict) else {}
        criteria = acceptance_criteria if acceptance_criteria is not None else contract.get("criteria", [])
        criteria = [str(item).strip() for item in (criteria or []) if str(item).strip()]
        proof = str(proof_required or contract.get("proof_required") or PROOF_RESPONSE).strip().lower()
        contract.update({
            "criteria": criteria,
            "proof_required": proof if proof in PROOF_KINDS else PROOF_RESPONSE,
            "version": 1,
        })
        meta["completion_contract"] = contract
        self.conn.execute(
            """
            INSERT INTO tasks(task_id, title, spec, status, parent_task_id, dependencies_json,
                              workspace, model_override, metadata_json, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                str(title or "").strip(),
                str(spec or ""),
                status,
                parent_task_id,
                json.dumps(dependencies or [], ensure_ascii=False),
                workspace,
                model_override,
                json.dumps(meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_task(task_id) or {}

    def _task_from_row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["dependencies"] = _json_list(item.pop("dependencies_json", "[]"))
        item["metadata"] = _json_dict(item.pop("metadata_json", "{}"))
        return item

    def _run_from_row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["evidence"] = _json_list(item.pop("evidence_json", "[]"))
        return item

    def get_task(self, task_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        item = self._task_from_row(row)
        item["runs"] = self.list_runs(task_id=task_id)
        blocked_by = self.unfinished_dependencies(item["dependencies"])
        item["blocked_by"] = blocked_by
        item["runnable"] = item["status"] in TASK_CLAIMABLE_STATUSES and not blocked_by
        return item

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        if status:
            rows = self.conn.execute(
                "SELECT task_id FROM tasks WHERE status=? ORDER BY updated DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT task_id FROM tasks ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [item for row in rows if (item := self.get_task(str(row["task_id"])))]

    def unfinished_dependencies(self, dependencies: list[str] | None) -> list[str]:
        deps = [str(item) for item in (dependencies or []) if str(item)]
        if not deps:
            return []
        placeholders = ",".join("?" for _ in deps)
        rows = self.conn.execute(
            f"SELECT task_id, status FROM tasks WHERE task_id IN ({placeholders})", deps
        ).fetchall()
        states = {str(row["task_id"]): str(row["status"]) for row in rows}
        return [task_id for task_id in deps if states.get(task_id) != TASK_STATUS_DONE]

    def retry_task(self, task_id: str) -> dict | None:
        row = self.conn.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or str(row["status"]) == TASK_STATUS_RUNNING:
            return None
        now = int(time.time())
        self.conn.execute(
            "UPDATE tasks SET status=?, updated=? WHERE task_id=?",
            (TASK_STATUS_QUEUED, now, task_id),
        )
        self.conn.commit()
        return self.get_task(task_id)

    def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        session_id: str = "",
        ttl_seconds: int = 900,
    ) -> dict | None:
        now = int(time.time())
        expires = now + max(1, int(ttl_seconds or 900))
        run_id = "trun-" + make_id()
        lease_token = make_id("lease-")
        lock = _conn_lock(self.conn)
        with lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT status, dependencies_json FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is None or str(row["status"]) not in TASK_CLAIMABLE_STATUSES:
                    self.conn.execute("ROLLBACK")
                    return None
                if self.unfinished_dependencies(_json_list(row["dependencies_json"])):
                    self.conn.execute("ROLLBACK")
                    return None
                statuses = tuple(sorted(TASK_CLAIMABLE_STATUSES))
                placeholders = ",".join("?" for _ in statuses)
                cur = self.conn.execute(
                    f"UPDATE tasks SET status=?, updated=? WHERE task_id=? AND status IN ({placeholders})",
                    (TASK_STATUS_RUNNING, now, task_id, *statuses),
                )
                if cur.rowcount != 1:
                    self.conn.execute("ROLLBACK")
                    return None
                attempt_row = self.conn.execute(
                    "SELECT COUNT(*) AS count FROM task_runs WHERE task_id=?", (task_id,)
                ).fetchone()
                attempt = int(attempt_row["count"] or 0) + 1
                self.conn.execute(
                    """
                    INSERT INTO task_runs(
                        run_id, task_id, worker_id, session_id, status, claim_expires_at,
                        lease_token, heartbeat_at, attempt, evidence_json, completion_state,
                        completion_note, created, updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 'pending', '', ?, ?)
                    """,
                    (
                        run_id, task_id, worker_id, session_id, TASK_RUN_STATUS_RUNNING,
                        expires, lease_token, now, attempt, now, now,
                    ),
                )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM task_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def list_runs(self, task_id: str = "", status: str = "", limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), 200))
        where: list[str] = []
        args: list[object] = []
        if task_id:
            where.append("task_id=?")
            args.append(task_id)
        if status:
            where.append("status=?")
            args.append(status)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM task_runs {where_sql} ORDER BY created DESC, attempt DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def heartbeat(self, run_id: str, lease_token: str, ttl_seconds: int = 900) -> bool:
        now = int(time.time())
        expires = now + max(1, int(ttl_seconds or 900))
        cur = self.conn.execute(
            """
            UPDATE task_runs SET heartbeat_at=?, claim_expires_at=?, updated=?
            WHERE run_id=? AND status=? AND lease_token=?
            """,
            (now, expires, now, run_id, TASK_RUN_STATUS_RUNNING, lease_token),
        )
        self.conn.commit()
        return cur.rowcount == 1

    @staticmethod
    def evaluate_completion(contract: dict, result: str, evidence: list[dict]) -> tuple[bool, str]:
        proof = str(contract.get("proof_required") or PROOF_RESPONSE).lower()
        if proof not in PROOF_KINDS:
            proof = PROOF_RESPONSE
        if proof == PROOF_NONE:
            return True, "No machine proof required"
        if proof == PROOF_RESPONSE:
            ok = bool(str(result or "").strip())
            return ok, "Non-empty final response recorded" if ok else "A final response is required"
        usable = [item for item in evidence if isinstance(item, dict) and not item.get("is_error")]
        if proof == PROOF_TOOL:
            ok = any(str(item.get("kind")) == PROOF_TOOL for item in usable)
            return ok, "Successful tool evidence recorded" if ok else "A successful tool result is required"
        ok = any(str(item.get("kind")) == PROOF_ARTIFACT for item in usable)
        return ok, "Artifact evidence recorded" if ok else "A generated artifact is required"

    def complete(
        self,
        run_id: str,
        result: str = "",
        *,
        evidence: list[dict] | None = None,
        lease_token: str = "",
    ) -> bool:
        now = int(time.time())
        row = self.conn.execute(
            """
            SELECT tr.task_id, tr.status, tr.lease_token, t.metadata_json
            FROM task_runs tr JOIN tasks t ON t.task_id=tr.task_id
            WHERE tr.run_id=?
            """,
            (run_id,),
        ).fetchone()
        if row is None or str(row["status"]) != TASK_RUN_STATUS_RUNNING:
            return False
        if lease_token and str(row["lease_token"] or "") != lease_token:
            return False
        recorded = [dict(item) for item in (evidence or []) if isinstance(item, dict)]
        if str(result or "").strip():
            recorded.append({"kind": PROOF_RESPONSE, "summary": str(result)[:500]})
        metadata = _json_dict(row["metadata_json"])
        contract = metadata.get("completion_contract")
        contract = contract if isinstance(contract, dict) else {"proof_required": PROOF_RESPONSE}
        verified, note = self.evaluate_completion(contract, result, recorded)
        if not verified:
            error = f"Completion contract not satisfied: {note}"
            self._finish(
                run_id,
                TASK_RUN_STATUS_FAILED,
                task_status=TASK_STATUS_BLOCKED,
                error=error,
                error_fingerprint=self.fingerprint_error(error),
                evidence=recorded,
                completion_state="rejected",
                completion_note=note,
                lease_token=lease_token,
                now=now,
            )
            return False
        return self._finish(
            run_id,
            TASK_RUN_STATUS_SUCCEEDED,
            task_status=TASK_STATUS_DONE,
            result=result,
            evidence=recorded,
            completion_state="verified",
            completion_note=note,
            lease_token=lease_token,
            now=now,
        )

    def fail(
        self,
        run_id: str,
        error: str,
        error_fingerprint: str = "",
        *,
        evidence: list[dict] | None = None,
        lease_token: str = "",
    ) -> bool:
        return self._finish(
            run_id,
            TASK_RUN_STATUS_FAILED,
            task_status=TASK_STATUS_BLOCKED,
            error=error,
            error_fingerprint=error_fingerprint or self.fingerprint_error(error),
            evidence=evidence or [],
            completion_state="failed",
            completion_note=str(error or "")[:500],
            lease_token=lease_token,
        )

    def _finish(
        self,
        run_id: str,
        status: str,
        *,
        task_status: str,
        result: str = "",
        error: str = "",
        error_fingerprint: str = "",
        evidence: list[dict] | None = None,
        completion_state: str = "",
        completion_note: str = "",
        lease_token: str = "",
        now: int | None = None,
    ) -> bool:
        now = int(now or time.time())
        lock = _conn_lock(self.conn)
        with lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT task_id, status, lease_token FROM task_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if row is None or str(row["status"]) != TASK_RUN_STATUS_RUNNING:
                    self.conn.execute("ROLLBACK")
                    return False
                if lease_token and str(row["lease_token"] or "") != lease_token:
                    self.conn.execute("ROLLBACK")
                    return False
                cur = self.conn.execute(
                    """
                    UPDATE task_runs SET status=?, result=?, error=?, error_fingerprint=?,
                        evidence_json=?, completion_state=?, completion_note=?, updated=?
                    WHERE run_id=? AND status=?
                    """,
                    (
                        status, result, error, error_fingerprint,
                        json.dumps(evidence or [], ensure_ascii=False), completion_state,
                        completion_note, now, run_id, TASK_RUN_STATUS_RUNNING,
                    ),
                )
                if cur.rowcount != 1:
                    self.conn.execute("ROLLBACK")
                    return False
                self.conn.execute(
                    "UPDATE tasks SET status=?, updated=? WHERE task_id=?",
                    (task_status, now, row["task_id"]),
                )
                self.conn.execute("COMMIT")
                return True
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    def mark_stale(self, now: int | None = None) -> int:
        now = int(now or time.time())
        lock = _conn_lock(self.conn)
        with lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                rows = self.conn.execute(
                    """
                    SELECT run_id, task_id FROM task_runs
                    WHERE status=? AND claim_expires_at > 0 AND claim_expires_at < ?
                    """,
                    (TASK_RUN_STATUS_RUNNING, now),
                ).fetchall()
                for row in rows:
                    self.conn.execute(
                        """
                        UPDATE task_runs SET status=?, completion_state='stale',
                            completion_note='Lease expired', updated=?
                        WHERE run_id=? AND status=?
                        """,
                        (TASK_RUN_STATUS_STALE, now, row["run_id"], TASK_RUN_STATUS_RUNNING),
                    )
                    self.conn.execute(
                        "UPDATE tasks SET status=?, updated=? WHERE task_id=? AND status=?",
                        (TASK_STATUS_STALE, now, row["task_id"], TASK_STATUS_RUNNING),
                    )
                self.conn.execute("COMMIT")
                return len(rows)
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    def summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        counts = {str(row["status"]): int(row["count"] or 0) for row in rows}
        now = int(time.time())
        expiring = self.conn.execute(
            """
            SELECT COUNT(*) FROM task_runs
            WHERE status=? AND claim_expires_at > 0 AND claim_expires_at <= ?
            """,
            (TASK_RUN_STATUS_RUNNING, now + 60),
        ).fetchone()[0]
        return {
            "queued": counts.get(TASK_STATUS_QUEUED, 0),
            "running": counts.get(TASK_STATUS_RUNNING, 0),
            "blocked": counts.get(TASK_STATUS_BLOCKED, 0),
            "stale": counts.get(TASK_STATUS_STALE, 0),
            "done": counts.get(TASK_STATUS_DONE, 0),
            "lease_at_risk": int(expiring or 0),
        }

    @staticmethod
    def fingerprint_error(error: str) -> str:
        normalized = " ".join(str(error or "").lower().split())[:500]
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
