"""Markdown note CRUD operations."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from hushclaw.util.ids import make_id


class MarkdownStore:
    """Read/write persistent Markdown notes with YAML-like front-matter."""

    def __init__(self, notes_dir: Path, conn: sqlite3.Connection) -> None:
        self.notes_dir = notes_dir
        self.conn = conn

    def _today_dir(self) -> Path:
        from datetime import date
        d = date.today().isoformat()
        p = self.notes_dir / d
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _slug(text: str, max_len: int = 40) -> str:
        text = text.lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text).strip("-")
        return text[:max_len] or "note"

    def _parse_frontmatter(self, raw: str) -> tuple[dict, str]:
        """Parse minimal --- front-matter block."""
        if not raw.startswith("---"):
            return {}, raw
        end = raw.find("\n---", 3)
        if end == -1:
            return {}, raw
        fm_text = raw[3:end].strip()
        body = raw[end + 4:].strip()
        meta: dict = {}
        for line in fm_text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        return meta, body

    def _render_frontmatter(self, meta: dict) -> str:
        lines = ["---"]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        return "\n".join(lines)

    def write_note(
        self,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        scope: str = "global",
        persist_to_disk: bool = True,
        note_type: str = "fact",
        memory_kind: str = "project_knowledge",
    ) -> str:
        """Write a note and index it in SQLite. Returns note_id.

        persist_to_disk=False skips writing the .md file; the note still lives
        in SQLite (FTS + vectors + note_bodies) and is fully searchable.
        Use False for machine-generated fragments (auto-extract) to avoid
        cluttering the notes/ directory with low-value machine output.
        """
        note_id = make_id()
        tags = tags or []
        title = title or content[:60].split("\n")[0]
        now = int(time.time())

        if persist_to_disk:
            slug = self._slug(title)
            path = self._today_dir() / f"{note_id[:8]}-{slug}.md"
            meta = {
                "note_id": note_id,
                "title": title,
                "tags": json.dumps(tags),
                "created": now,
                "modified": now,
            }
            full = f"{self._render_frontmatter(meta)}\n\n{content}\n"
            path.write_text(full, encoding="utf-8")
            path_str = str(path)
        else:
            path_str = ""

        self.conn.execute(
            "INSERT INTO notes (note_id, path, title, tags, created, modified, scope, note_type, memory_kind) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (note_id, path_str, title, json.dumps(tags), now, now, scope, note_type, memory_kind),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO note_bodies (note_id, body) VALUES (?,?)",
            (note_id, content),
        )
        # Update FTS manually (trigger uses note_bodies join which may not fire correctly)
        self.conn.execute(
            "INSERT INTO notes_fts(rowid, note_id, title, body, tags) "
            "SELECT rowid, note_id, title, ?, ? FROM notes WHERE note_id=?",
            (content, json.dumps(tags), note_id),
        )
        self.conn.commit()
        return note_id

    def read_note(self, note_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT n.*, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id) WHERE n.note_id=?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def update_note(self, note_id: str, content: str, tags: list[str] | None = None) -> bool:
        row = self.conn.execute(
            "SELECT * FROM notes WHERE note_id=?", (note_id,)
        ).fetchone()
        if row is None:
            return False
        now = int(time.time())
        path = Path(row["path"])
        meta = {
            "note_id": note_id,
            "title": row["title"],
            "tags": json.dumps(tags if tags is not None else json.loads(row["tags"])),
            "created": row["created"],
            "modified": now,
        }
        full = f"{self._render_frontmatter(meta)}\n\n{content}\n"
        path.write_text(full, encoding="utf-8")

        tags_json = json.dumps(tags) if tags is not None else row["tags"]
        self.conn.execute(
            "UPDATE notes SET modified=?, tags=? WHERE note_id=?",
            (now, tags_json, note_id),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO note_bodies (note_id, body) VALUES (?,?)",
            (note_id, content),
        )
        self.conn.commit()
        return True

    def delete_note(self, note_id: str) -> bool:
        row = self.conn.execute(
            "SELECT path FROM notes WHERE note_id=?", (note_id,)
        ).fetchone()
        if row is None:
            return False
        path_str = row["path"]
        if path_str:  # empty path means no .md file (persist_to_disk=False notes)
            path = Path(path_str)
            if path.is_file():
                path.unlink()
        self.conn.execute("DELETE FROM notes WHERE note_id=?", (note_id,))
        self.conn.commit()
        return True
