from pathlib import Path

from hushclaw.memory.conversations import ConversationBindingStore
from hushclaw.memory.db import open_db
from hushclaw.memory.outbox import DeliveryOutboxStore
from hushclaw.os_contracts import AgentOSOutboundMessage, ConversationAddress, ConversationBinding


def test_conversation_bindings_are_persisted_and_upserted(tmp_path: Path):
    conn = open_db(tmp_path)
    store = ConversationBindingStore(conn)
    address = ConversationAddress(provider="telegram", account_id="bot-1", conversation_id="chat-7")
    first = ConversationBinding(address=address, session_id="c-old", agent="default")
    store.upsert(first)
    assert store.get(address).session_id == "c-old"

    second = ConversationBinding(
        address=address,
        session_id="c-new",
        workspace="work",
        metadata={"source": "test"},
    )
    store.upsert(second)
    assert store.get(address).to_dict() == second.to_dict()
    assert conn.execute("SELECT count(*) FROM conversation_bindings").fetchone()[0] == 1
    conn.close()


def test_existing_database_gets_additive_binding_table(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = open_db(tmp_path)
    conn.execute("DROP TABLE conversation_bindings")
    conn.commit()
    conn.close()

    # Reopening simulates an installation whose old schema predates this table.
    conn = open_db(tmp_path)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_bindings'"
    ).fetchone() is not None
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 3
    conn.close()
    assert db_path.exists()


def test_delivery_outbox_is_idempotent_and_tracks_terminal_state(tmp_path: Path):
    conn = open_db(tmp_path)
    store = DeliveryOutboxStore(conn)
    message = AgentOSOutboundMessage(
        address=ConversationAddress(provider="telegram", conversation_id="chat-7"),
        body="hello",
        session_id="s-1",
        idempotency_key="telegram:event-42:reply",
    )
    first = store.enqueue(message)
    second = store.enqueue(message)
    assert first.delivery_id == second.delivery_id
    delivered = store.mark_delivered(first.delivery_id, "external-9")
    assert delivered.status == "delivered"
    row = conn.execute(
        "SELECT status, attempt_count, external_message_id FROM delivery_outbox WHERE delivery_id=?",
        (first.delivery_id,),
    ).fetchone()
    assert dict(row) == {
        "status": "delivered",
        "attempt_count": 1,
        "external_message_id": "external-9",
    }
    conn.close()
