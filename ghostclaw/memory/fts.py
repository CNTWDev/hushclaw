"""FTS5 full-text search over notes."""
from __future__ import annotations

import json
import sqlite3


class FTSSearch:
    """BM25 full-text search using SQLite FTS5."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Return notes matching query, ranked by BM25 score."""
        if not query.strip():
            return []
        # Escape FTS5 special chars
        safe_q = query.replace('"', '""')
        try:
            rows = self.conn.execute(
                """
                SELECT n.note_id, n.title, n.tags, n.created, n.modified,
                       b.body,
                       bm25(notes_fts) AS score
                FROM notes_fts
                JOIN notes n ON notes_fts.note_id = n.note_id
                JOIN note_bodies b ON b.note_id = n.note_id
                WHERE notes_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (safe_q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Query syntax error — try simple prefix match
            words = query.split()
            safe_q = " ".join(f'"{w}"' for w in words if w)
            try:
                rows = self.conn.execute(
                    """
                    SELECT n.note_id, n.title, n.tags, n.created, n.modified,
                           b.body,
                           bm25(notes_fts) AS score
                    FROM notes_fts
                    JOIN notes n ON notes_fts.note_id = n.note_id
                    JOIN note_bodies b ON b.note_id = n.note_id
                    WHERE notes_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (safe_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        return [
            {
                "note_id": r["note_id"],
                "title": r["title"],
                "tags": json.loads(r["tags"] or "[]"),
                "body": r["body"],
                "score_fts": abs(float(r["score"])),
            }
            for r in rows
        ]
