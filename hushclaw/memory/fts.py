"""FTS5 full-text search over notes."""
from __future__ import annotations

import json
import sqlite3


class FTSSearch:
    """BM25 full-text search using SQLite FTS5."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(
        self,
        query: str,
        limit: int = 10,
        scopes: list[str] | None = None,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Return notes matching query, ranked by BM25 score."""
        if not query.strip():
            return []
        # Escape FTS5 special chars
        safe_q = query.replace('"', '""')

        extra_clause = ""
        extra_params: tuple = ()
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            extra_clause += f" AND n.scope IN ({placeholders})"
            extra_params += tuple(scopes)
        if exclude_tags:
            placeholders = ",".join("?" * len(exclude_tags))
            extra_clause += (
                f" AND NOT EXISTS ("
                f"SELECT 1 FROM json_each(n.tags) "
                f"WHERE json_each.value IN ({placeholders}))"
            )
            extra_params += tuple(exclude_tags)

        def _run(q: str) -> list:
            return self.conn.execute(
                f"""
                SELECT n.note_id, n.title, n.tags, n.created, n.modified,
                       b.body,
                       bm25(notes_fts) AS score
                FROM notes_fts
                JOIN notes n ON notes_fts.note_id = n.note_id
                JOIN note_bodies b ON b.note_id = n.note_id
                WHERE notes_fts MATCH ?{extra_clause}
                ORDER BY score
                LIMIT ?
                """,
                (q,) + extra_params + (limit,),
            ).fetchall()

        try:
            rows = _run(safe_q)
        except sqlite3.OperationalError:
            # Query syntax error — try simple prefix match
            safe_q = " ".join(f'"{w}"' for w in query.split() if w)
            try:
                rows = _run(safe_q)
            except sqlite3.OperationalError:
                return []

        return [
            {
                "note_id": r["note_id"],
                "title": r["title"],
                "tags": json.loads(r["tags"] or "[]"),
                "body": r["body"],
                "created": r["created"],
                "score_fts": abs(float(r["score"])),
            }
            for r in rows
        ]
