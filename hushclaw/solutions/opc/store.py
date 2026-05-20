"""SQLite-backed OPC product store."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hushclaw.util.ids import make_id


_RECORD_TYPES = {"employee", "team", "channel", "message", "goal", "work_item", "discussion"}


class OpcStore:
    """Persist OPC records in the local memory database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def _get(self, record_type: str, record_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT record_id, payload_json, created, updated FROM opc_records "
            "WHERE record_type=? AND record_id=?",
            (record_type, record_id),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return {
            **payload,
            "id": row["record_id"],
            "created": int(row["created"] or payload.get("created") or 0),
            "updated": int(row["updated"] or payload.get("updated") or 0),
        }

    def get(self, record_type: str, record_id: str) -> dict | None:
        self._validate_type(record_type)
        return self._get(record_type, record_id)

    def list(self, record_type: str, *, limit: int = 200) -> list[dict]:
        self._validate_type(record_type)
        rows = self.conn.execute(
            "SELECT record_id, payload_json, created, updated FROM opc_records "
            "WHERE record_type=? ORDER BY updated DESC LIMIT ?",
            (record_type, max(1, int(limit))),
        ).fetchall()
        items: list[dict] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            items.append({
                **payload,
                "id": row["record_id"],
                "created": int(row["created"] or payload.get("created") or 0),
                "updated": int(row["updated"] or payload.get("updated") or 0),
            })
        return items

    def upsert(self, record_type: str, record_id: str, payload: dict[str, Any]) -> dict:
        self._validate_type(record_type)
        record_id = str(record_id or "").strip() or make_id(f"{record_type}-")
        now = self._now()
        existing = self._get(record_type, record_id)
        created = int((existing or {}).get("created") or now)
        item = {
            **(existing or {}),
            **payload,
            "id": record_id,
            "created": created,
            "updated": now,
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO opc_records "
            "(record_type, record_id, payload_json, created, updated) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                record_type,
                record_id,
                json.dumps(item, ensure_ascii=False),
                created,
                now,
            ),
        )
        self.conn.commit()
        return item

    def delete(self, record_type: str, record_id: str) -> bool:
        self._validate_type(record_type)
        cur = self.conn.execute(
            "DELETE FROM opc_records WHERE record_type=? AND record_id=?",
            (record_type, record_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _validate_type(record_type: str) -> None:
        if record_type not in _RECORD_TYPES:
            raise ValueError(f"unsupported OPC record type: {record_type}")
