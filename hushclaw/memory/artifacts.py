"""ArtifactStore: disk-backed storage for large tool outputs and binary content.

Large payloads (screenshots, downloaded files, long tool results) are written to
disk under data_dir/artifacts/. The DB stores only metadata + a short summary;
the event log records artifact_id pointers instead of raw content.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from hushclaw.util.ids import make_id


class ArtifactStore:
    """Save and retrieve artifacts; keep DB rows small."""

    def __init__(self, conn: sqlite3.Connection, data_dir: Path) -> None:
        self.conn = conn
        self.artifacts_dir = data_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        session_id: str,
        content: str | bytes,
        *,
        tool_name: str = "",
        mime_type: str = "text/plain",
        summary: str = "",
    ) -> str:
        """Write content to disk and record metadata. Returns artifact_id."""
        artifact_id = make_id("art-")
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        path = self.artifacts_dir / artifact_id
        path.write_bytes(content_bytes)
        content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
        self.conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, session_id, tool_name, storage_path, size_bytes, mime_type, summary, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, session_id, tool_name, str(path),
             len(content_bytes), mime_type, (summary or content_bytes[:200].decode("utf-8", errors="replace"))[:500],
             int(time.time())),
        )
        self.conn.commit()
        _ = content_hash  # available for future dedup
        return artifact_id

    def load(self, artifact_id: str) -> bytes | None:
        """Load raw bytes for an artifact, or None if not found."""
        row = self.conn.execute(
            "SELECT storage_path FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        path = Path(row["storage_path"])
        return path.read_bytes() if path.exists() else None

    def metadata(self, artifact_id: str) -> dict | None:
        """Return artifact metadata dict, or None if not found."""
        row = self.conn.execute(
            "SELECT artifact_id, session_id, tool_name, storage_path, size_bytes, mime_type, summary, created "
            "FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        return dict(row) if row else None

    def session_artifacts(self, session_id: str, limit: int = 100) -> list[dict]:
        """Return all artifact metadata for a session."""
        rows = self.conn.execute(
            "SELECT artifact_id, session_id, tool_name, storage_path, size_bytes, mime_type, summary, created "
            "FROM artifacts WHERE session_id=? ORDER BY created DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
