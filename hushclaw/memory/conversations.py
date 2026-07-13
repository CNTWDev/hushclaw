"""Persistent external-conversation to Agent OS session bindings."""
from __future__ import annotations

import json
import sqlite3
import time

from hushclaw.os_contracts import ConversationAddress, ConversationBinding
from hushclaw.util.ids import make_id


class ConversationBindingStore:
    """Small storage adapter; the rest of the app need not know SQL details."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, address: ConversationAddress) -> ConversationBinding | None:
        row = self.conn.execute(
            "SELECT * FROM conversation_bindings "
            "WHERE provider=? AND account_id=? AND conversation_id=? AND thread_id=?",
            address.key(),
        ).fetchone()
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        return ConversationBinding(
            address=address,
            session_id=str(row["session_id"]),
            workspace=str(row["workspace"] or ""),
            agent=str(row["agent"] or ""),
            external_user_id=str(row["external_user_id"] or ""),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def upsert(self, binding: ConversationBinding) -> ConversationBinding:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO conversation_bindings "
            "(binding_id, provider, account_id, conversation_id, thread_id, session_id, "
            "workspace, agent, external_user_id, metadata_json, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, account_id, conversation_id, thread_id) DO UPDATE SET "
            "session_id=excluded.session_id, workspace=excluded.workspace, agent=excluded.agent, "
            "external_user_id=excluded.external_user_id, metadata_json=excluded.metadata_json, "
            "updated=excluded.updated",
            (
                make_id("cb-"), *binding.address.key(), binding.session_id,
                binding.workspace, binding.agent, binding.external_user_id,
                json.dumps(binding.metadata, ensure_ascii=False, sort_keys=True), now, now,
            ),
        )
        self.conn.commit()
        return binding
