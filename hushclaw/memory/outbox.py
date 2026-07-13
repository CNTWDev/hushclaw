"""Durable outbound delivery outbox."""
from __future__ import annotations

import json
import sqlite3
import time

from hushclaw.os_contracts import AgentOSOutboundMessage, DeliveryReceipt
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
        return DeliveryReceipt(
            delivery_id=str(row["delivery_id"]),
            status=str(row["status"]),
            external_message_id=str(row["external_message_id"] or ""),
            error=str(row["last_error"] or ""),
        )

    def mark_delivered(self, delivery_id: str, external_message_id: str = "") -> DeliveryReceipt:
        now = int(time.time())
        self.conn.execute(
            "UPDATE delivery_outbox SET status='delivered', attempt_count=attempt_count+1, "
            "external_message_id=?, last_error='', updated=? WHERE delivery_id=?",
            (external_message_id, now, delivery_id),
        )
        self.conn.commit()
        return DeliveryReceipt(delivery_id, "delivered", external_message_id=external_message_id)

    def mark_failed(self, delivery_id: str, error: str) -> DeliveryReceipt:
        now = int(time.time())
        self.conn.execute(
            "UPDATE delivery_outbox SET status='failed', attempt_count=attempt_count+1, "
            "last_error=?, updated=? WHERE delivery_id=?",
            (str(error)[:2000], now, delivery_id),
        )
        self.conn.commit()
        return DeliveryReceipt(delivery_id, "failed", error=str(error))
