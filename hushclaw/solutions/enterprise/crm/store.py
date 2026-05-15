"""SQLite-backed lightweight CRM fact/event store."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hushclaw.util.ids import make_id


CRM_ENTITY_TYPES = {"account", "contact", "lead", "opportunity", "activity", "pipeline_stage"}


class CRMStore:
    """Minimal CRM store optimized for Agent tools and event replay."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, entity_type: str, data: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        entity_type = self._entity_type(entity_type)
        now = int(time.time() * 1000)
        entity_id = str(data.get("id") or data.get(f"{entity_type}_id") or make_id(f"crm-{entity_type}-"))
        existing = self.get(entity_type, entity_id)
        payload = {**(existing or {}), **dict(data), "id": entity_id}
        created = int((existing or {}).get("created") or now)
        payload["created"] = created
        payload["updated"] = now
        self.conn.execute(
            "INSERT OR REPLACE INTO crm_records "
            "(entity_type, entity_id, payload_json, created, updated) VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, json.dumps(payload, ensure_ascii=False), created, now),
        )
        event_type = f"crm.{entity_type}.{'updated' if existing else 'created'}"
        self.append_event(entity_type, entity_id, event_type, payload, actor_id=actor_id)
        if entity_type == "lead" and not existing:
            self.suggest_next_action("lead", entity_id, actor_id=actor_id)
        self.conn.commit()
        return payload

    def get(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        entity_type = self._entity_type(entity_type)
        row = self.conn.execute(
            "SELECT payload_json FROM crm_records WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchone()
        if row is None:
            return None
        return self._json(row["payload_json"])

    def list(self, entity_type: str, *, limit: int = 50) -> list[dict[str, Any]]:
        entity_type = self._entity_type(entity_type)
        rows = self.conn.execute(
            "SELECT payload_json FROM crm_records WHERE entity_type=? ORDER BY updated DESC LIMIT ?",
            (entity_type, max(1, int(limit))),
        ).fetchall()
        return [self._json(row["payload_json"]) for row in rows]

    def search(self, query: str, *, entity_type: str = "", limit: int = 20) -> list[dict[str, Any]]:
        q = str(query or "").lower()
        types = [self._entity_type(entity_type)] if entity_type else sorted(CRM_ENTITY_TYPES)
        results: list[dict[str, Any]] = []
        for typ in types:
            for item in self.list(typ, limit=200):
                haystack = json.dumps(item, ensure_ascii=False).lower()
                if not q or q in haystack:
                    results.append({"entity_type": typ, **item})
                if len(results) >= int(limit):
                    return results
        return results

    def append_event(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor_id: str = "",
    ) -> dict[str, Any]:
        event = {
            "event_id": make_id("crm-ev-"),
            "entity_type": self._entity_type(entity_type),
            "entity_id": str(entity_id),
            "event_type": str(event_type),
            "payload": dict(payload),
            "actor_id": str(actor_id or ""),
            "ts": int(time.time() * 1000),
        }
        self.conn.execute(
            "INSERT INTO crm_events "
            "(event_id, entity_type, entity_id, event_type, payload_json, actor_id, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_id"],
                event["entity_type"],
                event["entity_id"],
                event["event_type"],
                json.dumps(event["payload"], ensure_ascii=False),
                event["actor_id"],
                event["ts"],
            ),
        )
        return event

    def events(self, *, entity_type: str = "", entity_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_type:
            clauses.append("entity_type=?")
            params.append(self._entity_type(entity_type))
        if entity_id:
            clauses.append("entity_id=?")
            params.append(str(entity_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            "SELECT event_id, entity_type, entity_id, event_type, payload_json, actor_id, ts "
            f"FROM crm_events {where} ORDER BY ts DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "event_type": row["event_type"],
                "payload": self._json(row["payload_json"]),
                "actor_id": row["actor_id"],
                "ts": row["ts"],
            }
            for row in rows
        ]

    def next_actions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.working_state(state_type="next_action", status="suggested", limit=limit)

    def suggest_next_action(
        self,
        entity_type: str,
        entity_id: str,
        *,
        actor_id: str = "",
    ) -> dict[str, Any]:
        recent = [
            event for event in self.events(entity_type=entity_type, entity_id=entity_id, limit=5)
            if event["event_type"] != "agent.next_action.suggested"
        ]
        if not recent:
            suggestion = "Create the first activity and confirm ownership."
        elif recent[0]["event_type"] == "crm.activity.logged":
            suggestion = "Review the latest activity and schedule a follow-up."
        elif "stage_changed" in recent[0]["event_type"]:
            suggestion = "Validate stage risks and update the next customer commitment."
        elif entity_type == "lead":
            suggestion = "Qualify the lead, confirm owner, and decide the first follow-up."
        else:
            suggestion = "Check ownership, recent changes, and decide the next follow-up."
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "suggestion": suggestion,
            "recent_events": recent,
        }
        state = self.upsert_working_state(
            "next_action",
            entity_type,
            entity_id,
            payload,
            status="suggested",
            actor_id=actor_id,
        )
        event = self.append_event(
            entity_type,
            entity_id,
            "agent.next_action.suggested",
            {"state_id": state["state_id"], **payload},
            actor_id=actor_id,
        )
        self.conn.commit()
        return {"event_id": event["event_id"], **state, **payload}

    def upsert_working_state(
        self,
        state_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        *,
        status: str = "suggested",
        actor_id: str = "",
        state_id: str = "",
    ) -> dict[str, Any]:
        entity_type = self._entity_type(entity_type)
        state_type = str(state_type or "").strip()
        if not state_type:
            raise ValueError("state_type cannot be empty")
        now = int(time.time() * 1000)
        row = None
        if state_id:
            row = self.conn.execute(
                "SELECT created FROM crm_working_state WHERE state_id=?",
                (str(state_id),),
            ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT state_id, created FROM crm_working_state "
                "WHERE entity_type=? AND entity_id=? AND state_type=? AND status=? "
                "ORDER BY updated DESC LIMIT 1",
                (entity_type, str(entity_id), state_type, str(status or "suggested")),
            ).fetchone()
        resolved_id = str(state_id or (row["state_id"] if row and "state_id" in row.keys() else "") or make_id("crm-state-"))
        created = int(row["created"] if row else now)
        state = {
            "state_id": resolved_id,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "state_type": state_type,
            "status": str(status or "suggested"),
            "payload": dict(payload),
            "actor_id": str(actor_id or ""),
            "created": created,
            "updated": now,
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO crm_working_state "
            "(state_id, entity_type, entity_id, state_type, status, payload_json, actor_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                state["state_id"],
                state["entity_type"],
                state["entity_id"],
                state["state_type"],
                state["status"],
                json.dumps(state["payload"], ensure_ascii=False),
                state["actor_id"],
                state["created"],
                state["updated"],
            ),
        )
        return state

    def update_working_state_status(
        self,
        state_id: str,
        status: str,
        *,
        actor_id: str = "",
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT state_id, entity_type, entity_id, state_type, payload_json, actor_id, created "
            "FROM crm_working_state WHERE state_id=?",
            (str(state_id),),
        ).fetchone()
        if row is None:
            return None
        now = int(time.time() * 1000)
        payload = self._json(row["payload_json"])
        self.conn.execute(
            "UPDATE crm_working_state SET status=?, actor_id=?, updated=? WHERE state_id=?",
            (str(status), str(actor_id or row["actor_id"] or ""), now, str(state_id)),
        )
        event_type = f"agent.{row['state_type']}.{status}"
        self.append_event(
            row["entity_type"],
            row["entity_id"],
            event_type,
            {"state_id": str(state_id), "status": str(status), **payload},
            actor_id=actor_id,
        )
        self.conn.commit()
        return {
            "state_id": row["state_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "state_type": row["state_type"],
            "status": str(status),
            "payload": payload,
            "actor_id": str(actor_id or row["actor_id"] or ""),
            "created": row["created"],
            "updated": now,
        }

    def working_state(
        self,
        *,
        state_type: str = "",
        status: str = "suggested",
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if state_type:
            clauses.append("state_type=?")
            params.append(str(state_type))
        if status:
            clauses.append("status=?")
            params.append(str(status))
        if entity_type:
            clauses.append("entity_type=?")
            params.append(self._entity_type(entity_type))
        if entity_id:
            clauses.append("entity_id=?")
            params.append(str(entity_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            "SELECT state_id, entity_type, entity_id, state_type, status, payload_json, actor_id, created, updated "
            f"FROM crm_working_state {where} ORDER BY updated DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "state_id": row["state_id"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "state_type": row["state_type"],
                "status": row["status"],
                "payload": self._json(row["payload_json"]),
                "actor_id": row["actor_id"],
                "created": row["created"],
                "updated": row["updated"],
            }
            for row in rows
        ]

    def _entity_type(self, entity_type: str) -> str:
        typ = str(entity_type or "").strip().lower()
        if typ not in CRM_ENTITY_TYPES:
            raise ValueError(f"Unknown CRM entity type: {entity_type}")
        return typ

    @staticmethod
    def _json(raw: str) -> dict[str, Any]:
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}
