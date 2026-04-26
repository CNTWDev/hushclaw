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
    summary  TEXT NOT NULL DEFAULT '',
    trajectory TEXT NOT NULL DEFAULT '',
    signals  TEXT NOT NULL DEFAULT '[]',
    last_consolidated INTEGER NOT NULL DEFAULT 0,
    dirty    INTEGER NOT NULL DEFAULT 1,
    updated  INTEGER NOT NULL,
    PRIMARY KEY (domain, scope)
);

CREATE TABLE IF NOT EXISTS calendar_events (
    event_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    all_day     INTEGER NOT NULL DEFAULT 0,
    color       TEXT NOT NULL DEFAULT 'indigo',
    attendees   TEXT NOT NULL DEFAULT '[]',
    source      TEXT NOT NULL DEFAULT 'local',
    remote_uid  TEXT NOT NULL DEFAULT '',
    remote_href TEXT NOT NULL DEFAULT '',
    remote_etag TEXT NOT NULL DEFAULT '',
    recurrence_id TEXT NOT NULL DEFAULT '',
    remote_calendar TEXT NOT NULL DEFAULT '',
    last_seen_at INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL,
    updated     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_time
ON calendar_events(start_time, end_time);

CREATE TABLE IF NOT EXISTS caldav_sync_state (
    sync_key          TEXT PRIMARY KEY,
    last_attempt      INTEGER NOT NULL DEFAULT 0,
    last_success      INTEGER NOT NULL DEFAULT 0,
    last_failure      INTEGER NOT NULL DEFAULT 0,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT NOT NULL DEFAULT '',
    last_result_count INTEGER NOT NULL DEFAULT 0,
    updated           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS caldav_collection_state (
    collection_key    TEXT PRIMARY KEY,
    last_ctag         TEXT NOT NULL DEFAULT '',
    last_sync_token   TEXT NOT NULL DEFAULT '',
    last_scan_at      INTEGER NOT NULL DEFAULT 0,
    last_result_count INTEGER NOT NULL DEFAULT 0,
    updated           INTEGER NOT NULL
);

-- Append-only event log: source of truth for session/thread/run replay.
-- thread_id and run_id are '' until Thread/Run layers are introduced (Phase 3).
-- status: 'completed' (default) | 'pending' (before execution) | 'failed'
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    thread_id    TEXT NOT NULL DEFAULT '',
    run_id       TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    artifact_id  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'completed',
    ts           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS events_session ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS events_thread  ON events(thread_id, ts)
    WHERE thread_id != '';
CREATE INDEX IF NOT EXISTS events_run     ON events(run_id, ts)
    WHERE run_id != '';

-- Artifact store: large tool outputs, screenshots, downloaded files.
-- Actual content lives on disk at storage_path; DB stores metadata + summary only.
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL DEFAULT '',
    tool_name    TEXT NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL DEFAULT '',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    mime_type    TEXT NOT NULL DEFAULT 'text/plain',
    summary      TEXT NOT NULL DEFAULT '',
    created      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS artifacts_session ON artifacts(session_id, created);

-- Thread: a persistent conversation branch within a session.
-- One thread per session initially; sub-agents create child threads.
CREATE TABLE IF NOT EXISTS threads (
    thread_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    parent_thread_id TEXT NOT NULL DEFAULT '',
    agent_name       TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    created          INTEGER NOT NULL,
    updated          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS threads_session ON threads(session_id);

-- Run: one execution of event_stream within a thread.
-- trigger_type: 'user' | 'scheduled' | 'sub_agent' | 'pipeline'
-- status: 'running' | 'completed' | 'failed' | 'cancelled'
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    trigger_type TEXT NOT NULL DEFAULT 'user',
    status       TEXT NOT NULL DEFAULT 'running',
    created      INTEGER NOT NULL,
    updated      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_thread  ON runs(thread_id, created);
CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id, created);

-- Projection cursors: track which events each projection has processed.
-- Allows ProjectionWorker to resume after restart without reprocessing old events.
CREATE TABLE IF NOT EXISTS projections (
    name    TEXT PRIMARY KEY,
    last_ts INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_blobs (
    blob_id      TEXT PRIMARY KEY,
    sha256       TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    mime_type    TEXT NOT NULL DEFAULT '',
    created      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS file_blobs_sha256 ON file_blobs(sha256);

CREATE TABLE IF NOT EXISTS uploaded_files (
    file_id       TEXT PRIMARY KEY,
    blob_id       TEXT NOT NULL,
    original_name TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'upload',
    created       INTEGER NOT NULL,
    last_used     INTEGER NOT NULL,
    deleted       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(blob_id) REFERENCES file_blobs(blob_id)
);

CREATE INDEX IF NOT EXISTS uploaded_files_blob_id ON uploaded_files(blob_id);
CREATE INDEX IF NOT EXISTS uploaded_files_last_used ON uploaded_files(last_used);

CREATE TABLE IF NOT EXISTS kb_file_index (
    blob_id        TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    note_id        TEXT NOT NULL DEFAULT '',
    indexed        INTEGER NOT NULL DEFAULT 0,
    created        INTEGER NOT NULL,
    updated        INTEGER NOT NULL,
    PRIMARY KEY (blob_id, parser_version)
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
    "CREATE TABLE IF NOT EXISTS belief_models (domain TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global', latest TEXT NOT NULL DEFAULT '', entries TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '', trajectory TEXT NOT NULL DEFAULT '', signals TEXT NOT NULL DEFAULT '[]', last_consolidated INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 1, updated INTEGER NOT NULL, PRIMARY KEY (domain, scope))",
    "ALTER TABLE belief_models ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE belief_models ADD COLUMN trajectory TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE belief_models ADD COLUMN signals TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE belief_models ADD COLUMN last_consolidated INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE belief_models ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1",
    # calendar_events table
    "CREATE TABLE IF NOT EXISTS calendar_events (event_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', start_time TEXT NOT NULL, end_time TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0, color TEXT NOT NULL DEFAULT 'indigo', attendees TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, updated INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_events_time ON calendar_events(start_time, end_time)",
    # source column: 'local' = AI/user created (never overwritten by CalDAV sync);
    #                'caldav' = pulled from external CalDAV (sync may overwrite)
    "ALTER TABLE calendar_events ADD COLUMN source TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE calendar_events ADD COLUMN remote_uid TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE calendar_events ADD COLUMN remote_href TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE calendar_events ADD COLUMN remote_etag TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE calendar_events ADD COLUMN recurrence_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE calendar_events ADD COLUMN remote_calendar TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE calendar_events ADD COLUMN last_seen_at INTEGER NOT NULL DEFAULT 0",
    # One-time fix: old CalDAV sync stored UTC times without the Z suffix
    # (e.g. "2026-04-22T09:00:00" instead of "2026-04-22T09:00:00Z").
    # Length=19 precisely matches YYYY-MM-DDTHH:MM:SS; date-only all-day events
    # (length=10, no T) are untouched. Idempotent: after migration length=20.
    "UPDATE calendar_events SET start_time = start_time || 'Z' WHERE instr(start_time, 'T') > 0 AND length(start_time) = 19",
    "UPDATE calendar_events SET end_time   = end_time   || 'Z' WHERE instr(end_time,   'T') > 0 AND length(end_time)   = 19",
    "CREATE TABLE IF NOT EXISTS caldav_sync_state (sync_key TEXT PRIMARY KEY, last_attempt INTEGER NOT NULL DEFAULT 0, last_success INTEGER NOT NULL DEFAULT 0, last_failure INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', last_result_count INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS caldav_collection_state (collection_key TEXT PRIMARY KEY, last_ctag TEXT NOT NULL DEFAULT '', last_sync_token TEXT NOT NULL DEFAULT '', last_scan_at INTEGER NOT NULL DEFAULT 0, last_result_count INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL)",
    "ALTER TABLE caldav_collection_state ADD COLUMN last_sync_token TEXT NOT NULL DEFAULT ''",
    # Phase 1: unified event log (append-only source of truth for replay)
    "CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '', type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', artifact_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'completed', ts INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS events_session ON events(session_id, ts)",
    "CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id, ts) WHERE thread_id != ''",
    "CREATE INDEX IF NOT EXISTS events_run ON events(run_id, ts) WHERE run_id != ''",
    # Phase 2: artifact store
    "CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY, session_id TEXT NOT NULL DEFAULT '', tool_name TEXT NOT NULL DEFAULT '', storage_path TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0, mime_type TEXT NOT NULL DEFAULT 'text/plain', summary TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS artifacts_session ON artifacts(session_id, created)",
    # Phase 3: thread and run tables
    "CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_thread_id TEXT NOT NULL DEFAULT '', agent_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active', created INTEGER NOT NULL, updated INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS threads_session ON threads(session_id)",
    "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, session_id TEXT NOT NULL, trigger_type TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'running', created INTEGER NOT NULL, updated INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, created)",
    "CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id, created)",
    # Phase 4: projection cursor tracking
    "CREATE TABLE IF NOT EXISTS projections (name TEXT PRIMARY KEY, last_ts INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL)",
    # Phase 7: step granularity in events
    "ALTER TABLE events ADD COLUMN step_id TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS events_step ON events(step_id, ts) WHERE step_id != ''",
    # Phase 9: security policies and retention rules
    "CREATE TABLE IF NOT EXISTS security_policies (policy_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default', data_class TEXT NOT NULL DEFAULT 'general', retention_days INTEGER NOT NULL DEFAULT 90, redact_patterns TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, updated INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS security_policies_tenant ON security_policies(tenant_id)",
    # Phase 11: composite cursor for ProjectionWorker (stable pagination on same-ts events)
    "ALTER TABLE projections ADD COLUMN last_event_id TEXT NOT NULL DEFAULT ''",
    # Phase 12: deduplicated uploaded file storage + KB index reuse
    "CREATE TABLE IF NOT EXISTS file_blobs (blob_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, storage_path TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0, mime_type TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS file_blobs_sha256 ON file_blobs(sha256)",
    "CREATE TABLE IF NOT EXISTS uploaded_files (file_id TEXT PRIMARY KEY, blob_id TEXT NOT NULL, original_name TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'upload', created INTEGER NOT NULL, last_used INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(blob_id) REFERENCES file_blobs(blob_id))",
    "CREATE INDEX IF NOT EXISTS uploaded_files_blob_id ON uploaded_files(blob_id)",
    "CREATE INDEX IF NOT EXISTS uploaded_files_last_used ON uploaded_files(last_used)",
    "CREATE TABLE IF NOT EXISTS kb_file_index (blob_id TEXT NOT NULL, parser_version TEXT NOT NULL, note_id TEXT NOT NULL DEFAULT '', indexed INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY (blob_id, parser_version))",
    # artifact_url: /files/ URL for generated files registered via write_file
    "ALTER TABLE uploaded_files ADD COLUMN artifact_url TEXT NOT NULL DEFAULT ''",
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
