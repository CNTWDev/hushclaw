"""SQLite connection management and schema initialization."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
    rowid        INTEGER PRIMARY KEY,
    note_id      TEXT UNIQUE NOT NULL,
    path         TEXT NOT NULL,
    title        TEXT,
    tags         TEXT DEFAULT '[]',
    created      INTEGER NOT NULL,
    modified     INTEGER NOT NULL,
    recall_count INTEGER NOT NULL DEFAULT 0,
    scope        TEXT NOT NULL DEFAULT 'global'
);

CREATE INDEX IF NOT EXISTS notes_scope ON notes(scope);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    title,
    body,
    tags
);

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    DELETE FROM notes_fts WHERE note_id = old.note_id;
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
    output_tokens INTEGER DEFAULT 0,
    workspace     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS turns_session ON turns(session, ts);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id        TEXT PRIMARY KEY,
    cron      TEXT NOT NULL,
    prompt    TEXT NOT NULL,
    agent     TEXT NOT NULL DEFAULT '',
    enabled   INTEGER NOT NULL DEFAULT 1,
    last_run  TEXT,
    created   TEXT NOT NULL,
    run_once  INTEGER NOT NULL DEFAULT 0,
    title     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS todos (
    todo_id  TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    notes    TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    due_at   INTEGER,
    tags     TEXT NOT NULL DEFAULT '[]',
    created  INTEGER NOT NULL,
    updated  INTEGER NOT NULL
);
"""

# Migrations for existing DBs (idempotent)
_MIGRATIONS = [
    "ALTER TABLE turns ADD COLUMN input_tokens INTEGER DEFAULT 0",
    "ALTER TABLE turns ADD COLUMN output_tokens INTEGER DEFAULT 0",
    "ALTER TABLE scheduled_tasks ADD COLUMN run_once INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scheduled_tasks ADD COLUMN title TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE notes ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'",
    "CREATE INDEX IF NOT EXISTS notes_scope ON notes(scope)",
    # Fix notes_ad trigger: use note_id instead of rowid to avoid SQL logic errors
    # when FTS5 rowids are out of sync with notes rowids.
    "DROP TRIGGER IF EXISTS notes_ad",
    """CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    DELETE FROM notes_fts WHERE note_id = old.note_id;
END""",
    "ALTER TABLE turns ADD COLUMN workspace TEXT NOT NULL DEFAULT ''",
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
    # Repair FTS5 if it was created with content='notes' (broken schema that causes
    # "no such column: T.body" on any FTS query). Detect by trying a COUNT; if it
    # fails, drop and recreate the FTS table as contentless and re-index everything.
    try:
        conn.execute("SELECT count(*) FROM notes_fts")
    except sqlite3.OperationalError:
        conn.executescript("""
            DROP TABLE IF EXISTS notes_fts;
            DROP TRIGGER IF EXISTS notes_ai;
            DROP TRIGGER IF EXISTS notes_ad;
            CREATE VIRTUAL TABLE notes_fts USING fts5(
                note_id UNINDEXED,
                title,
                body,
                tags
            );
            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                DELETE FROM notes_fts WHERE note_id = old.note_id;
            END;
        """)
        # Re-index all existing notes with their bodies
        conn.execute("""
            INSERT INTO notes_fts(rowid, note_id, title, body, tags)
            SELECT n.rowid, n.note_id, COALESCE(n.title, ''),
                   COALESCE(b.body, ''), COALESCE(n.tags, '[]')
            FROM notes n
            LEFT JOIN note_bodies b ON b.note_id = n.note_id
        """)
        conn.commit()
    return conn
