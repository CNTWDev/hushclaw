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
    scope        TEXT NOT NULL DEFAULT 'global',
    note_type    TEXT NOT NULL DEFAULT 'fact',
    memory_kind  TEXT NOT NULL DEFAULT 'project_knowledge'
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

CREATE TABLE IF NOT EXISTS sessions (
    session_id         TEXT PRIMARY KEY,
    parent_session_id  TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    workspace          TEXT NOT NULL DEFAULT '',
    created            INTEGER NOT NULL,
    updated            INTEGER NOT NULL,
    last_turn          INTEGER NOT NULL DEFAULT 0,
    turn_count         INTEGER NOT NULL DEFAULT 0,
    compaction_count   INTEGER NOT NULL DEFAULT 0,
    last_compacted_at  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS sessions_last_turn ON sessions(last_turn DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    turn_id UNINDEXED,
    session UNINDEXED,
    role UNINDEXED,
    content
);

CREATE TABLE IF NOT EXISTS session_lineage (
    lineage_id         TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    parent_session_id  TEXT NOT NULL DEFAULT '',
    relationship       TEXT NOT NULL,
    ts                 INTEGER NOT NULL,
    meta_json          TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS session_lineage_session ON session_lineage(session_id, ts DESC);

CREATE TABLE IF NOT EXISTS reflections (
    reflection_id      TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    task_fingerprint   TEXT NOT NULL,
    success            INTEGER NOT NULL DEFAULT 0,
    outcome            TEXT NOT NULL DEFAULT '',
    failure_mode       TEXT NOT NULL DEFAULT '',
    lesson             TEXT NOT NULL DEFAULT '',
    strategy_hint      TEXT NOT NULL DEFAULT '',
    skill_name         TEXT NOT NULL DEFAULT '',
    source_turn_count  INTEGER NOT NULL DEFAULT 0,
    created            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS reflections_task_fp
ON reflections(task_fingerprint, created DESC);

CREATE TABLE IF NOT EXISTS user_profile_facts (
    fact_id            TEXT PRIMARY KEY,
    category           TEXT NOT NULL,
    key                TEXT NOT NULL,
    value_json         TEXT NOT NULL DEFAULT '{}',
    confidence         REAL NOT NULL DEFAULT 0.5,
    source_session_id  TEXT NOT NULL DEFAULT '',
    updated            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS user_profile_category_key
ON user_profile_facts(category, key);

CREATE TABLE IF NOT EXISTS skill_outcomes (
    outcome_id         TEXT PRIMARY KEY,
    skill_name         TEXT NOT NULL,
    session_id         TEXT NOT NULL,
    task_fingerprint   TEXT NOT NULL DEFAULT '',
    success            INTEGER NOT NULL DEFAULT 0,
    note               TEXT NOT NULL DEFAULT '',
    quality_score      REAL NOT NULL DEFAULT 1.0,
    created            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS skill_outcomes_skill
ON skill_outcomes(skill_name, created DESC);

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

CREATE TABLE IF NOT EXISTS belief_models (
    domain   TEXT NOT NULL,
    scope    TEXT NOT NULL DEFAULT 'global',
    latest   TEXT NOT NULL DEFAULT '',
    entries  TEXT NOT NULL DEFAULT '[]',
    updated  INTEGER NOT NULL,
    PRIMARY KEY (domain, scope)
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
    "ALTER TABLE notes ADD COLUMN note_type TEXT NOT NULL DEFAULT 'fact'",
    "ALTER TABLE notes ADD COLUMN memory_kind TEXT NOT NULL DEFAULT 'project_knowledge'",
    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, parent_session_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL, last_turn INTEGER NOT NULL DEFAULT 0, turn_count INTEGER NOT NULL DEFAULT 0, compaction_count INTEGER NOT NULL DEFAULT 0, last_compacted_at INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS sessions_last_turn ON sessions(last_turn DESC)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(turn_id UNINDEXED, session UNINDEXED, role UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS session_lineage (lineage_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_session_id TEXT NOT NULL DEFAULT '', relationship TEXT NOT NULL, ts INTEGER NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS session_lineage_session ON session_lineage(session_id, ts DESC)",
    "CREATE TABLE IF NOT EXISTS reflections (reflection_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, task_fingerprint TEXT NOT NULL, success INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT '', failure_mode TEXT NOT NULL DEFAULT '', lesson TEXT NOT NULL DEFAULT '', strategy_hint TEXT NOT NULL DEFAULT '', skill_name TEXT NOT NULL DEFAULT '', source_turn_count INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS reflections_task_fp ON reflections(task_fingerprint, created DESC)",
    "CREATE TABLE IF NOT EXISTS user_profile_facts (fact_id TEXT PRIMARY KEY, category TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL DEFAULT '{}', confidence REAL NOT NULL DEFAULT 0.5, source_session_id TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS user_profile_category_key ON user_profile_facts(category, key)",
    "CREATE TABLE IF NOT EXISTS skill_outcomes (outcome_id TEXT PRIMARY KEY, skill_name TEXT NOT NULL, session_id TEXT NOT NULL, task_fingerprint TEXT NOT NULL DEFAULT '', success INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', quality_score REAL NOT NULL DEFAULT 1.0, created INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS skill_outcomes_skill ON skill_outcomes(skill_name, created DESC)",
    "ALTER TABLE skill_outcomes ADD COLUMN quality_score REAL NOT NULL DEFAULT 1.0",
    "CREATE TABLE IF NOT EXISTS belief_models (domain TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global', latest TEXT NOT NULL DEFAULT '', entries TEXT NOT NULL DEFAULT '[]', updated INTEGER NOT NULL, PRIMARY KEY (domain, scope))",
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
