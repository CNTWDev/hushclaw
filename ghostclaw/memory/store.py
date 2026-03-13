"""MemoryStore: unified facade over Markdown, FTS5, and vector search."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from ghostclaw.memory.db import open_db
from ghostclaw.memory.markdown import MarkdownStore
from ghostclaw.memory.fts import FTSSearch
from ghostclaw.memory.vectors import VectorStore
from ghostclaw.util.ids import make_id

# FTS score threshold above which vector search is skipped (saves embed cost)
_FTS_SHORTCUT_THRESHOLD = 0.8

# Recall cache TTL in seconds (same query within same session)
_CACHE_TTL = 30.0


class MemoryStore:
    """Single entry point for all memory operations."""

    def __init__(
        self,
        data_dir: Path,
        embed_provider: str = "local",
        api_key: str = "",
        fts_weight: float = 0.6,
        vec_weight: float = 0.4,
    ) -> None:
        self.data_dir = data_dir
        self.notes_dir = data_dir / "notes"
        self.sessions_dir = data_dir / "sessions"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self.fts_weight = fts_weight
        self.vec_weight = vec_weight

        self.conn: sqlite3.Connection = open_db(data_dir)
        self._md = MarkdownStore(self.notes_dir, self.conn)
        self._fts = FTSSearch(self.conn)
        self._vec = VectorStore(self.conn, embed_provider, api_key)

        # Session recall cache: (session_id, query) → (result_str, timestamp)
        self._recall_cache: dict[tuple[str, str], tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # Notes API
    # ------------------------------------------------------------------

    def remember(self, content: str, title: str = "", tags: list[str] | None = None) -> str:
        """Persist a note and index it. Returns note_id."""
        note_id = self._md.write_note(content, title=title, tags=tags)
        self._vec.index(note_id, f"{title}\n{content}")
        return note_id

    def get_note(self, note_id: str) -> dict | None:
        return self._md.read_note(note_id)

    def update_note(self, note_id: str, content: str, tags: list[str] | None = None) -> bool:
        ok = self._md.update_note(note_id, content, tags)
        if ok:
            self._vec.index(note_id, content)
        return ok

    def delete_note(self, note_id: str) -> bool:
        return self._md.delete_note(note_id)

    def search_by_tag(self, tag: str, limit: int = 10) -> list[dict]:
        """Return notes that carry the given tag (exact match in JSON array)."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? LIMIT ?",
            (tag, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_notes(self, limit: int = 100) -> list[dict]:
        """Return the most recently modified notes with their bodies."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.tags, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id) "
            "ORDER BY n.modified DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Hybrid FTS + vector search, merged by score."""
        fts_results = {r["note_id"]: r for r in self._fts.search(query, limit * 2)}
        vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2)}

        all_ids = set(fts_results) | set(vec_results)
        merged = []
        for nid in all_ids:
            fts_score = fts_results.get(nid, {}).get("score_fts", 0.0)
            vec_score = vec_results.get(nid, {}).get("score_vec", 0.0)
            combined = self.fts_weight * fts_score + self.vec_weight * vec_score
            note = fts_results.get(nid) or vec_results.get(nid, {})
            merged.append({
                "note_id": nid,
                "title": note.get("title", ""),
                "body": note.get("body", ""),
                "tags": note.get("tags", []),
                "score": combined,
            })

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    def recall(self, query: str, limit: int = 5) -> str:
        """Return a formatted string of top search results for LLM injection."""
        results = self.search(query, limit)
        if not results:
            return "No relevant memories found."
        parts = []
        for i, r in enumerate(results, 1):
            parts.append(f"[{i}] {r['title']}\n{r['body'][:300]}")
        return "\n\n".join(parts)

    def recall_with_budget(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.25,
        max_tokens: int = 800,
        session_id: str | None = None,
    ) -> str:
        """
        Token-budget-aware recall for LLM injection.

        FTS-first: if FTS scores are high, skips vector search.
        Score-gated: skips results below min_score.
        Budget-capped: stops injection at max_tokens (approx 1 token ≈ 4 chars).
        Session-cached: same query within same session cached for 30s.
        """
        # Check session cache
        cache_key = (session_id or "__global__", query)
        cached = self._recall_cache.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        # FTS-first strategy
        fts_results = self._fts.search(query, limit * 2)
        fts_max = max((r.get("score_fts", 0.0) for r in fts_results), default=0.0)

        if fts_max >= _FTS_SHORTCUT_THRESHOLD or not fts_results:
            # FTS is strong enough — skip vector search
            merged = [
                {
                    "note_id": r["note_id"],
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "score": self.fts_weight * r.get("score_fts", 0.0),
                }
                for r in fts_results
            ]
        else:
            # Full hybrid: FTS + vector
            vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2)}
            fts_map = {r["note_id"]: r for r in fts_results}
            all_ids = set(fts_map) | set(vec_results)
            merged = []
            for nid in all_ids:
                fts_s = fts_map.get(nid, {}).get("score_fts", 0.0)
                vec_s = vec_results.get(nid, {}).get("score_vec", 0.0)
                combined = self.fts_weight * fts_s + self.vec_weight * vec_s
                note = fts_map.get(nid) or vec_results.get(nid, {})
                merged.append({
                    "note_id": nid,
                    "title": note.get("title", ""),
                    "body": note.get("body", ""),
                    "score": combined,
                })

        # Score gate + sort
        merged.sort(key=lambda x: x["score"], reverse=True)
        filtered = [r for r in merged if r["score"] >= min_score]

        if not filtered:
            result = ""
        else:
            # Budget cap (approx 1 token ≈ 4 chars)
            parts: list[str] = []
            total_tokens = 0
            for r in filtered[:limit]:
                body = r["body"][:300]
                entry = f"[{r['title']}]\n{body}"
                entry_tokens = max(1, len(entry) // 4)
                if total_tokens + entry_tokens > max_tokens:
                    break
                parts.append(entry)
                total_tokens += entry_tokens
            result = "\n\n".join(parts)

        # Update session cache and evict stale entries
        now = time.time()
        self._recall_cache[cache_key] = (result, now)
        stale = [k for k, v in self._recall_cache.items() if now - v[1] >= _CACHE_TTL]
        for k in stale:
            del self._recall_cache[k]

        return result

    # ------------------------------------------------------------------
    # Session / Turn persistence
    # ------------------------------------------------------------------

    def save_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> str:
        turn_id = make_id()
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO turns (turn_id, session, role, content, tool_name, ts, "
            "input_tokens, output_tokens) VALUES (?,?,?,?,?,?,?,?)",
            (turn_id, session_id, role, content, tool_name, now, input_tokens, output_tokens),
        )
        self.conn.commit()
        # Also append to .jsonl file for human-readable history
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        turn_file = session_dir / "turns.jsonl"
        with open(turn_file, "a", encoding="utf-8") as f:
            record = {
                "turn_id": turn_id, "role": role, "content": content,
                "tool_name": tool_name, "ts": now,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return turn_id

    def update_turn_tokens(self, turn_id: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.conn.execute(
            "UPDATE turns SET input_tokens=?, output_tokens=? WHERE turn_id=?",
            (input_tokens, output_tokens, turn_id),
        )
        self.conn.commit()

    def load_session_turns(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM turns WHERE session=? ORDER BY ts",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_sessions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT session AS session_id, COUNT(*) AS turn_count, MIN(ts) AS started, MAX(ts) AS last_turn, "
            "SUM(input_tokens) AS total_input_tokens, SUM(output_tokens) AS total_output_tokens "
            "FROM turns GROUP BY session ORDER BY last_turn DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def save_session_summary(self, session_id: str, summary: str) -> None:
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "summary.md").write_text(summary, encoding="utf-8")

    def load_session_summary(self, session_id: str) -> str | None:
        p = self.sessions_dir / session_id / "summary.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------

    def add_scheduled_task(self, cron: str, prompt: str, agent: str = "") -> str:
        task_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO scheduled_tasks (id, cron, prompt, agent, created) VALUES (?,?,?,?,?)",
            (task_id, cron, prompt, agent, datetime.now().isoformat()),
        )
        self.conn.commit()
        return task_id

    def list_scheduled_tasks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY created"
        ).fetchall()
        return [dict(r) for r in rows]

    def cancel_scheduled_task(self, task_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (task_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_due_scheduled_tasks(self, now: datetime) -> list[dict]:
        from ghostclaw.scheduler import _cron_matches
        return [t for t in self.list_scheduled_tasks() if _cron_matches(t["cron"], now)]

    def update_scheduled_task_last_run(self, task_id: str, ts: datetime) -> None:
        self.conn.execute(
            "UPDATE scheduled_tasks SET last_run=? WHERE id=?",
            (ts.isoformat(), task_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
