from pathlib import Path

from hushclaw.memory.conversations import ConversationBindingStore
from hushclaw.memory.db import open_db
from hushclaw.os_contracts import ConversationAddress, ConversationBinding


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
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
    conn.close()
    assert db_path.exists()
