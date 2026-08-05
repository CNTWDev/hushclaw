"""Durable outbound delivery outbox with retry and dead-letter states."""
from __future__ import annotations

import json
import sqlite3
import time

from hushclaw.memory.events import _conn_lock
from hushclaw.os_contracts import (
    AgentOSOutboundMessage,
    ConversationAddress,
    DeliveryReceipt,
)
from hushclaw.util.ids import make_id


class DeliveryOutboxStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def enqueue(self, message: AgentOSOutboundMessage) -> DeliveryReceipt:
        now = int(time.time())
        delivery_id = make_id("del-")
        idempotency_key = message.idempotency_key or delivery_id
        self.conn.execute(
            "INSERT INTO delivery_outbox "
            "(delivery_id, provider, account_id, conversation_id, thread_id, session_id, "
            "message_type, body, payload_json, status, attempt_count, next_attempt_at, "
            "last_error, external_message_id, idempotency_key, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, '', '', ?, ?, ?) "
            "ON CONFLICT(idempotency_key) DO NOTHING",
            (
                delivery_id,
                message.address.provider,
                message.address.account_id,
                message.address.conversation_id,
                message.address.thread_id,
                message.session_id,
                message.message_type,
                message.body,
                json.dumps(message.metadata, ensure_ascii=False, sort_keys=True),
                idempotency_key,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT delivery_id, status, external_message_id, last_error "
            "FROM delivery_outbox WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        self.conn.commit()
        return self._receipt(row)

    @staticmethod
    def _receipt(row) -> DeliveryReceipt:
        return DeliveryReceipt(
            delivery_id=str(row["delivery_id"]),
            status=str(row["status"]),
            external_message_id=str(row["external_message_id"] or ""),
            error=str(row["last_error"] or ""),
        )

    def get(self, delivery_id: str) -> DeliveryReceipt | None:
        row = self.conn.execute(
            "SELECT delivery_id, status, external_message_id, last_error "
            "FROM delivery_outbox WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
        return self._receipt(row) if row else None

    def claim(self, delivery_id: str, now: int | None = None) -> bool:
        now = int(now or time.time())
        cur = self.conn.execute(
            """
            UPDATE delivery_outbox SET status='in_flight', updated=?
            WHERE delivery_id=? AND status IN ('pending', 'retry')
              AND (next_attempt_at=0 OR next_attempt_at<=?)
            """,
            (now, delivery_id, now),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def claim_due(
        self,
        providers: list[str] | tuple[str, ...],
        *,
        limit: int = 20,
        now: int | None = None,
    ) -> list[tuple[DeliveryReceipt, AgentOSOutboundMessage]]:
        providers = tuple(sorted({str(item) for item in providers if str(item)}))
        if not providers:
            return []
        now = int(now or time.time())
        limit = max(1, min(int(limit or 20), 100))
        placeholders = ",".join("?" for _ in providers)
        lock = _conn_lock(self.conn)
        with lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                rows = self.conn.execute(
                    f"""
                    SELECT * FROM delivery_outbox
                    WHERE provider IN ({placeholders})
                      AND status IN ('pending', 'retry')
                      AND (next_attempt_at=0 OR next_attempt_at<=?)
                    ORDER BY created ASC LIMIT ?
                    """,
                    (*providers, now, limit),
                ).fetchall()
                claimed = []
                for row in rows:
                    cur = self.conn.execute(
                        """
                        UPDATE delivery_outbox SET status='in_flight', updated=?
                        WHERE delivery_id=? AND status IN ('pending', 'retry')
                        """,
                        (now, row["delivery_id"]),
                    )
                    if cur.rowcount == 1:
                        claimed.append(row)
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        return [(self._receipt(row), self._message(row)) for row in claimed]

    @staticmethod
    def _message(row) -> AgentOSOutboundMessage:
        try:
            metadata = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        return AgentOSOutboundMessage(
            address=ConversationAddress(
                provider=str(row["provider"]),
                account_id=str(row["account_id"] or ""),
                conversation_id=str(row["conversation_id"]),
                thread_id=str(row["thread_id"] or ""),
            ),
            body=str(row["body"] or ""),
            session_id=str(row["session_id"] or ""),
            message_type=str(row["message_type"] or "text"),
            idempotency_key=str(row["idempotency_key"] or ""),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def recover_in_flight(self) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            """
            UPDATE delivery_outbox SET status='retry', next_attempt_at=?,
                last_error=CASE WHEN last_error='' THEN 'Recovered after process restart' ELSE last_error END,
                updated=? WHERE status='in_flight'
            """,
            (now, now),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def mark_delivered(self, delivery_id: str, external_message_id: str = "") -> DeliveryReceipt:
        now = int(time.time())
        self.conn.execute(
            "UPDATE delivery_outbox SET status='delivered', attempt_count=attempt_count+1, "
            "external_message_id=?, last_error='', next_attempt_at=0, updated=? WHERE delivery_id=?",
            (external_message_id, now, delivery_id),
        )
        self.conn.commit()
        return DeliveryReceipt(delivery_id, "delivered", external_message_id=external_message_id)

    def mark_failed(
        self,
        delivery_id: str,
        error: str,
        *,
        max_attempts: int = 5,
        now: int | None = None,
    ) -> DeliveryReceipt:
        now = int(now or time.time())
        row = self.conn.execute(
            "SELECT attempt_count FROM delivery_outbox WHERE delivery_id=?", (delivery_id,)
        ).fetchone()
        attempt = int(row["attempt_count"] or 0) + 1 if row else 1
        dead = attempt >= max(1, int(max_attempts or 5))
        status = "dead_letter" if dead else "retry"
        backoff = 0 if dead else min(300, 5 * (2 ** max(0, attempt - 1)))
        self.conn.execute(
            """
            UPDATE delivery_outbox SET status=?, attempt_count=?, next_attempt_at=?,
                last_error=?, updated=? WHERE delivery_id=?
            """,
            (status, attempt, now + backoff, str(error)[:2000], now, delivery_id),
        )
        self.conn.commit()
        return DeliveryReceipt(delivery_id, status, error=str(error))

    def summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM delivery_outbox GROUP BY status"
        ).fetchall()
        counts = {str(row["status"]): int(row["count"] or 0) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "in_flight": counts.get("in_flight", 0),
            "retry": counts.get("retry", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "delivered": counts.get("delivered", 0),
        }
