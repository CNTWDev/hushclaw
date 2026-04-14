"""MemoryStore: unified facade over Markdown, FTS5, and vector search."""
from __future__ import annotations

import json
import math
import random
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from hushclaw.memory.db import open_db
from hushclaw.memory.markdown import MarkdownStore
from hushclaw.memory.fts import FTSSearch
from hushclaw.memory.vectors import VectorStore
from hushclaw.util.ids import make_id

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

        if not (0.95 <= fts_weight + vec_weight <= 1.05):
            raise ValueError(
                f"fts_weight + vec_weight must sum to ~1.0, got {fts_weight + vec_weight:.3f}"
            )
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

    def remember(
        self,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        scope: str = "global",
        persist_to_disk: bool = True,
        note_type: str = "fact",
    ) -> str:
        """Persist a note and index it. Returns note_id.

        persist_to_disk=False stores the note in SQLite only (no .md file).
        """
        note_id = self._md.write_note(
            content, title=title, tags=tags, scope=scope,
            persist_to_disk=persist_to_disk, note_type=note_type,
        )
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

    def note_exists_with_title(self, title: str) -> bool:
        """Return True if any note with this exact title already exists."""
        row = self.conn.execute(
            "SELECT 1 FROM notes WHERE title=? LIMIT 1", (title,)
        ).fetchone()
        return row is not None

    def delete_by_scope(self, scope: str) -> int:
        """Delete all notes with the given scope. Returns the number deleted."""
        rows = self.conn.execute(
            "SELECT note_id FROM notes WHERE scope=?", (scope,)
        ).fetchall()
        count = 0
        for row in rows:
            if self._md.delete_note(row["note_id"]):
                count += 1
        return count

    def search_by_tag(self, tag: str, limit: int = 10) -> list[dict]:
        """Return notes that carry the given tag (exact match in JSON array)."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.recall_count, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? LIMIT ?",
            (tag, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_recall_count(self, note_id: str) -> None:
        """Increment the recall_count for a note (used to track skill usage)."""
        self.conn.execute(
            "UPDATE notes SET recall_count = recall_count + 1 WHERE note_id=?",
            (note_id,),
        )
        self.conn.commit()

    def get_promotion_candidates(self, threshold: int = 5, tag: str = "_skill") -> list[dict]:
        """Return skills with recall_count >= threshold, ordered by recall_count desc."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.recall_count, b.body "
            "FROM notes n LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? AND n.recall_count >= ? "
            "ORDER BY n.recall_count DESC",
            (tag, threshold),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_notes(
        self,
        limit: int = 100,
        offset: int = 0,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes with their bodies."""
        if exclude_tags:
            ph = ",".join("?" * len(exclude_tags))
            rows = self.conn.execute(
                f"SELECT n.note_id, n.title, n.tags, b.body FROM notes n "
                f"LEFT JOIN note_bodies b USING(note_id) "
                f"WHERE NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({ph})) "
                f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
                (*exclude_tags, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT n.note_id, n.title, n.tags, b.body FROM notes n "
                "LEFT JOIN note_bodies b USING(note_id) "
                "ORDER BY n.modified DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    def list_recent_notes_by_scopes(
        self,
        scopes: list[str],
        limit: int = 100,
        offset: int = 0,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes whose scope is in `scopes`."""
        scope_ph = ",".join("?" * len(scopes))
        if exclude_tags:
            tag_ph = ",".join("?" * len(exclude_tags))
            rows = self.conn.execute(
                f"SELECT n.note_id, n.title, n.tags, n.scope, b.body FROM notes n "
                f"LEFT JOIN note_bodies b USING(note_id) "
                f"WHERE n.scope IN ({scope_ph}) "
                f"AND NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({tag_ph})) "
                f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
                (*scopes, *exclude_tags, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT n.note_id, n.title, n.tags, n.scope, b.body FROM notes n "
                f"LEFT JOIN note_bodies b USING(note_id) "
                f"WHERE n.scope IN ({scope_ph}) "
                f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
                (*scopes, limit, offset),
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
        decay_rate: float = 0.0,
        retrieval_temperature: float = 0.0,
        scopes: list[str] | None = None,
        max_age_days: int = 0,
        exclude_types: set[str] | None = None,
    ) -> str:
        """
        Token-budget-aware recall for LLM injection.

        FTS-first: if FTS scores are high, skips vector search.
        Score-gated: skips results below min_score.
        Budget-capped: stops injection at max_tokens (approx 1 token ≈ 4 chars).
        Session-cached: same query within same session cached for 30s.
        decay_rate: exponential time-decay λ; score × e^(-λ × age_days). 0.0 = no decay.
        retrieval_temperature: softmax temperature for random sampling. 0.0 = deterministic top-k.
        scopes: list of scope values to filter (e.g. ["global", "agent:researcher"]).
                None = no filter (all scopes returned).
        max_age_days: drop notes older than N days from recall. 0 = no limit.
        """
        # Cache key includes creativity params and scopes so different modes don't collide
        cache_key = (session_id or "__global__", query, decay_rate, retrieval_temperature,
                     tuple(sorted(scopes)) if scopes else None, max_age_days,
                     tuple(sorted(exclude_types)) if exclude_types else None)
        cached = self._recall_cache.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        # Internal system notes are never surfaced as recalled memories.
        # _compact_archive: raw conversation dumps (huge, noisy).
        _exclude = ["_compact_archive"]

        # Empty query = serendipity random sampling from all notes
        if not query.strip():
            merged = self._random_sample_notes(limit * 2, scopes=scopes)
        else:
            # FTS-first strategy
            fts_results = self._fts.search(query, limit * 2, scopes=scopes,
                                           exclude_tags=_exclude)
            fts_max = max((r.get("score_fts", 0.0) for r in fts_results), default=0.0)

            if fts_results and fts_max >= _FTS_SHORTCUT_THRESHOLD:
                # FTS is confident enough — skip vector search to save cost
                merged = [
                    {
                        "note_id": r["note_id"],
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "created": r.get("created"),
                        "score": self.fts_weight * r.get("score_fts", 0.0),
                    }
                    for r in fts_results
                ]
            else:
                # Full hybrid: FTS + vector
                vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2, scopes=scopes,
                                                                          exclude_tags=_exclude)}
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
                        "created": note.get("created"),
                        "score": combined,
                    })

        # Apply time-decay penalty and max_age_days filter
        if decay_rate > 0.0 or max_age_days > 0:
            now_ts = time.time()
            cutoff_ts = (now_ts - max_age_days * 86400.0) if max_age_days > 0 else 0.0
            kept = []
            for r in merged:
                created = r.get("created") or now_ts
                if max_age_days > 0 and created < cutoff_ts:
                    continue  # too old — drop from recall pool
                if decay_rate > 0.0:
                    age_days = (now_ts - created) / 86400.0
                    r["score"] = r["score"] * math.exp(-decay_rate * age_days)
                kept.append(r)
            merged = kept

        # recall_count boost and note_type filter/boost — single batch DB query.
        _TYPE_BOOST = {"interest": 1.10, "belief": 1.10, "preference": 1.10}
        if merged:
            note_ids = [r["note_id"] for r in merged]
            placeholders = ",".join("?" * len(note_ids))
            rc_rows = self.conn.execute(
                f"SELECT note_id, recall_count, note_type FROM notes WHERE note_id IN ({placeholders})",
                note_ids,
            ).fetchall()
            rc_map = {row["note_id"]: (row["recall_count"], row["note_type"] or "fact") for row in rc_rows}
            kept_after_type = []
            for r in merged:
                rc, note_type = rc_map.get(r["note_id"], (0, "fact"))
                # Exclude blocked types (e.g. action_log)
                if exclude_types and note_type in exclude_types:
                    continue
                if rc > 0:
                    r["score"] = r["score"] * (1.0 + 0.1 * math.log1p(rc))
                # Boost user-modeling types
                type_mult = _TYPE_BOOST.get(note_type, 1.0)
                if type_mult != 1.0:
                    r["score"] = r["score"] * type_mult
                kept_after_type.append(r)
            merged = kept_after_type

        # Score gate
        filtered = [r for r in merged if r["score"] >= min_score]

        # Sort or softmax-weighted random sample
        if retrieval_temperature > 0.0 and len(filtered) > 1:
            temp = max(retrieval_temperature, 1e-6)
            weights = [math.exp(r["score"] / temp) for r in filtered]
            w_sum = sum(weights)
            probs = [w / w_sum for w in weights]
            k = min(limit, len(filtered))
            indices = list(range(len(filtered)))
            chosen: list[dict] = []
            remaining_probs = list(probs)
            for _ in range(k):
                if not indices:
                    break
                r_sum = sum(remaining_probs[i] for i in indices)
                if r_sum <= 0:
                    break
                roll = random.random() * r_sum
                cum = 0.0
                picked = indices[0]
                for idx in indices:
                    cum += remaining_probs[idx]
                    if roll <= cum:
                        picked = idx
                        break
                chosen.append(filtered[picked])
                indices.remove(picked)
            filtered = chosen
        else:
            filtered = sorted(filtered, key=lambda r: r["score"], reverse=True)

        if not filtered:
            result = ""
        else:
            # Budget cap (approx 1 token ≈ 4 chars)
            parts: list[str] = []
            recalled_ids: list[str] = []
            total_tokens = 0
            for r in filtered[:limit]:
                body = r["body"][:300]
                entry = f"[{r['title']}]\n{body}"
                entry_tokens = max(1, len(entry) // 4)
                if max_tokens > 0 and (total_tokens + entry_tokens > max_tokens):
                    break
                parts.append(entry)
                recalled_ids.append(r["note_id"])
                total_tokens += entry_tokens
            result = "\n\n".join(parts)

            # Increment recall_count for notes that actually appeared in the output.
            # Batched update; ignore errors (e.g. note deleted mid-flight).
            if recalled_ids:
                placeholders = ",".join("?" * len(recalled_ids))
                try:
                    self.conn.execute(
                        f"UPDATE notes SET recall_count = recall_count + 1 "
                        f"WHERE note_id IN ({placeholders})",
                        recalled_ids,
                    )
                    self.conn.commit()
                except Exception:
                    pass

        # Update session cache and evict stale entries
        now = time.time()
        self._recall_cache[cache_key] = (result, now)
        stale = [k for k, v in self._recall_cache.items() if now - v[1] >= _CACHE_TTL]
        for k in stale:
            del self._recall_cache[k]

        return result

    def _random_sample_notes(self, limit: int, scopes: list[str] | None = None) -> list[dict]:
        """Return random notes for serendipity injection. All get score=1.0."""
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"SELECT n.note_id, n.title, n.created, b.body "
                f"FROM notes n JOIN note_bodies b USING(note_id) "
                f"WHERE n.scope IN ({placeholders}) "
                f"ORDER BY RANDOM() LIMIT ?",
                tuple(scopes) + (limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT n.note_id, n.title, n.created, b.body "
                "FROM notes n JOIN note_bodies b USING(note_id) "
                "ORDER BY RANDOM() LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "note_id": r["note_id"],
                "title": r["title"] or "",
                "body": r["body"] or "",
                "created": r["created"],
                "score": 1.0,
            }
            for r in rows
        ]

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
        workspace: str = "",
    ) -> str:
        turn_id = make_id()
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO turns (turn_id, session, role, content, tool_name, ts, "
            "input_tokens, output_tokens, workspace) VALUES (?,?,?,?,?,?,?,?,?)",
            (turn_id, session_id, role, content, tool_name, now, input_tokens, output_tokens, workspace or ""),
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

    @staticmethod
    def _clip_text(text: str, limit: int = 56) -> str:
        s = " ".join((text or "").split())
        if not s:
            return ""
        return s if len(s) <= limit else s[:limit].rstrip() + "…"

    @staticmethod
    def _session_kind(session_id: str) -> str:
        if session_id.startswith("sched_"):
            return "scheduled"
        if session_id.startswith("auto_"):
            return "auto"
        if session_id.startswith("broadcast_"):
            return "broadcast"
        return "chat"

    @staticmethod
    def _fallback_title(session_id: str) -> str:
        if session_id.startswith("sched_"):
            return "Scheduled task"
        if session_id.startswith("broadcast_"):
            return "Broadcast run"
        if session_id.startswith("auto_"):
            return "Auto session"
        return f"Session {session_id[-8:]}"

    def list_sessions(
        self,
        limit: int = 200,
        include_scheduled: bool = True,
        max_idle_days: int = 0,
        workspace: str | None = None,
    ) -> list[dict]:
        now_ts = int(time.time())
        cutoff_ts = now_ts - max_idle_days * 86400 if max_idle_days > 0 else 0
        where = []
        params: list[object] = []
        if not include_scheduled:
            where.append("t.session NOT LIKE 'sched_%'")
        if cutoff_ts > 0:
            where.append("t.ts >= ?")
            params.append(cutoff_ts)
        if workspace is not None:
            where.append("t.workspace = ?")
            params.append(workspace)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT t.session AS session_id, COUNT(*) AS turn_count, MIN(t.ts) AS started, MAX(t.ts) AS last_turn, "
            "SUM(t.input_tokens) AS total_input_tokens, SUM(t.output_tokens) AS total_output_tokens, "
            "(SELECT tu.content FROM turns tu WHERE tu.session=t.session AND tu.role='user' ORDER BY tu.ts ASC LIMIT 1) AS first_user_text, "
            "(SELECT tl.content FROM turns tl WHERE tl.session=t.session ORDER BY tl.ts DESC LIMIT 1) AS last_content "
            f"FROM turns t {where_sql} "
            "GROUP BY t.session ORDER BY last_turn DESC LIMIT ?"
        )
        params.append(max(1, int(limit)))
        rows = self.conn.execute(sql, tuple(params)).fetchall()

        # scheduled session title lookup: sched_<taskIdPrefix>
        task_rows = self.conn.execute(
            "SELECT id, title FROM scheduled_tasks WHERE title IS NOT NULL AND title != ''"
        ).fetchall()
        task_title_by_prefix = {
            str(r["id"])[:8]: str(r["title"]).strip()
            for r in task_rows
            if r["id"] and r["title"]
        }

        out = []
        for r in rows:
            d = dict(r)
            session_id = str(d.get("session_id") or "")
            first_user = d.get("first_user_text") or ""
            last_content = d.get("last_content") or ""
            kind = self._session_kind(session_id)

            title = ""
            if kind == "scheduled":
                prefix = session_id[6:14] if len(session_id) >= 14 else ""
                if prefix:
                    title = task_title_by_prefix.get(prefix, "")
            if not title:
                title = self._clip_text(first_user, 56) or self._fallback_title(session_id)

            d["title"] = title
            d["last_preview"] = self._clip_text(last_content, 72)
            d["kind"] = kind
            d.pop("first_user_text", None)
            d.pop("last_content", None)
            out.append(d)
        return out

    def delete_session(self, session_id: str) -> bool:
        """Delete all turns for a session from the DB and its summary file."""
        self.conn.execute("DELETE FROM turns WHERE session=?", (session_id,))
        self.conn.commit()
        session_dir = self.sessions_dir / session_id
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
        return True

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

    def add_scheduled_task(
        self, cron: str, prompt: str, agent: str = "",
        run_once: bool = False, title: str = "",
    ) -> str:
        task_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO scheduled_tasks (id, cron, prompt, agent, created, run_once, title) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, cron, prompt, agent, datetime.now().isoformat(),
             1 if run_once else 0, title),
        )
        self.conn.commit()
        return task_id

    def list_scheduled_tasks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM scheduled_tasks ORDER BY created"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_active_scheduled_tasks(self) -> list[dict]:
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

    def toggle_scheduled_task(self, task_id: str, enabled: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=? WHERE id=?",
            (1 if enabled else 0, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_scheduled_task(self, task_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_due_scheduled_tasks(self, now: datetime) -> list[dict]:
        from hushclaw.scheduler import _cron_matches
        return [t for t in self.list_active_scheduled_tasks() if _cron_matches(t["cron"], now)]

    def update_scheduled_task_last_run(self, task_id: str, ts: datetime) -> None:
        self.conn.execute(
            "UPDATE scheduled_tasks SET last_run=? WHERE id=?",
            (ts.isoformat(), task_id),
        )
        self.conn.commit()

    def disable_run_once_task(self, task_id: str) -> None:
        self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (task_id,)
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Todos
    # ------------------------------------------------------------------

    def add_todo(
        self,
        title: str,
        notes: str = "",
        priority: int = 0,
        due_at: int | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        import time as _time
        todo_id = "td-" + make_id()
        now = int(_time.time())
        tags_json = json.dumps(tags or [])
        self.conn.execute(
            "INSERT INTO todos (todo_id, title, notes, status, priority, due_at, tags, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (todo_id, title, notes, "pending", priority, due_at, tags_json, now, now),
        )
        self.conn.commit()
        return self.get_todo(todo_id)

    def get_todo(self, todo_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d

    def list_todos(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM todos WHERE status=? ORDER BY priority DESC, created ASC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM todos ORDER BY priority DESC, created ASC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            result.append(d)
        return result

    def update_todo(self, todo_id: str, **fields) -> dict | None:
        import time as _time
        allowed = {"title", "notes", "status", "priority", "due_at", "tags"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_todo(todo_id)
        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = json.dumps(updates["tags"])
        updates["updated"] = int(_time.time())
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [todo_id]
        self.conn.execute(
            f"UPDATE todos SET {set_clause} WHERE todo_id=?", values
        )
        self.conn.commit()
        return self.get_todo(todo_id)

    def delete_todo(self, todo_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM todos WHERE todo_id=?", (todo_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()
