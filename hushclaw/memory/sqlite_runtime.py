"""SQLite connection helpers for local memory storage."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


def configure_sqlite_connection(conn: sqlite3.Connection, *, readonly: bool = False) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size = -32768")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 134217728")
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class SQLiteReadConnections:
    """Thread-local readonly connections for parallel recall/search paths."""

    def __init__(self, data_dir: Path) -> None:
        self.db_path = Path(data_dir) / "memory.db"
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
            configure_sqlite_connection(conn, readonly=True)
            self._local.conn = conn
        return conn
