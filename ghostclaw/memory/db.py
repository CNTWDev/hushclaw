"""SQLite connection management and schema initialization."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
    rowid    INTEGER PRIMARY KEY,
    note_id  TEXT UNIQUE NOT NULL,
    path     TEXT NOT NULL,
    title    TEXT,
    tags     TEXT DEFAULT '[]',
    created  INTEGER NOT NULL,
    modified INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    title,
    body,
    tags,
    content='notes',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, note_id, title, body, tags)
    SELECT new.rowid, new.note_id, new.title,
           (SELECT body FROM note_bodies WHERE note_id=new.note_id LIMIT 1),
           new.tags;
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, note_id, title, body, tags)
    VALUES('delete', old.rowid, old.note_id, old.title, '', old.tags);
END;

CREATE TABLE IF NOT EXISTS note_bodies (
    note_id TEXT PRIMARY KEY REFERENCES notes(note_id) ON DELETE CASCADE,
    body    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS embeddings (
    note_id TEXT PRIMARY KEY REFERENCES notes(note_id) ON DELETE CASCADE,
    model   TEXT NOT NULL,
    dim     INTEGER NOT NULL,
    vec     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id       TEXT PRIMARY KEY,
    session       TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    tool_name     TEXT,
    ts            INTEGER NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS turns_session ON turns(session, ts);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id        TEXT PRIMARY KEY,
    cron      TEXT NOT NULL,
    prompt    TEXT NOT NULL,
    agent     TEXT NOT NULL DEFAULT '',
    enabled   INTEGER NOT NULL DEFAULT 1,
    last_run  TEXT,
    created   TEXT NOT NULL
);
"""

# Migrations for existing DBs (idempotent)
_MIGRATIONS = [
    "ALTER TABLE turns ADD COLUMN input_tokens INTEGER DEFAULT 0",
    "ALTER TABLE turns ADD COLUMN output_tokens INTEGER DEFAULT 0",
]


def open_db(data_dir: Path) -> sqlite3.Connection:
    """Open (and initialize) the SQLite database."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "memory.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Initialize schema
    conn.executescript(_SCHEMA)
    # Apply migrations (idempotent)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    return conn
