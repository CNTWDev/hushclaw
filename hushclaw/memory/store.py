"""MemoryStore: unified facade over Markdown, FTS5, and vector search."""
from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from hushclaw.memory.db import open_db
from hushclaw.memory.markdown import MarkdownStore
from hushclaw.memory.user_profile import UserProfileStore
from hushclaw.memory.fts import FTSSearch
from hushclaw.memory.kinds import (
    RECALL_MEMORY_KINDS,
    SYSTEM_MEMORY_TAGS,
    USER_VISIBLE_MEMORY_KINDS,
    infer_memory_kind,
)
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
        self.user_profile = UserProfileStore(self.conn)

        # Session recall cache: (session_id, query) → (result_str, timestamp)
        self._recall_cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._backfill_sessions()
        self._backfill_turns_fts()

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
        memory_kind: str = "",
    ) -> str:
        """Persist a note and index it. Returns note_id.

        persist_to_disk=False stores the note in SQLite only (no .md file).
        """
        tags = tags or []
        resolved_kind = infer_memory_kind(
            note_type=note_type,
            tags=tags,
            memory_kind=memory_kind,
        )
        note_id = self._md.write_note(
            content, title=title, tags=tags, scope=scope,
            persist_to_disk=persist_to_disk, note_type=note_type, memory_kind=resolved_kind,
        )
        self._vec.index(note_id, f"{title}\n{content}")
        # Auto-aggregate belief/interest notes into belief_models.
        # _auto_extract tag is a UI visibility filter only — it does NOT block
        # belief/interest signals from feeding the domain knowledge model.
        if note_type in {"belief", "interest"}:
            domain = self._extract_domain_from_tags(tags)
            self._append_to_belief_model(domain, scope, note_id, content, note_type)
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

    # ------------------------------------------------------------------
    # Belief models API
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_domain_from_tags(tags: list[str]) -> str:
        """Return the domain from a 'domain:xxx' tag, or 'general'."""
        for t in tags:
            if t.startswith("domain:"):
                domain = t[7:].strip()
                if domain:
                    return domain
        return "general"

    def _append_to_belief_model(
        self, domain: str, scope: str, note_id: str, content: str, note_type: str
    ) -> None:
        """Upsert belief_model for (domain, scope): update latest + prepend entry (keep last 10)."""
        now = int(time.time())
        row = self.conn.execute(
            "SELECT entries FROM belief_models WHERE domain=? AND scope=?",
            (domain, scope),
        ).fetchone()
        entries: list[dict] = json.loads(row["entries"]) if row else []
        entries.insert(0, {
            "ts": now,
            "note_id": note_id,
            "content": content,
            "note_type": note_type,
        })
        entries = entries[:10]
        self.conn.execute(
            """INSERT INTO belief_models (domain, scope, latest, entries, summary, trajectory, signals, last_consolidated, dirty, updated)
               VALUES (?, ?, ?, ?, '', '', '[]', 0, 1, ?)
               ON CONFLICT(domain, scope) DO UPDATE SET
                 latest=excluded.latest, entries=excluded.entries, dirty=1, updated=excluded.updated""",
            (domain, scope, content, json.dumps(entries, ensure_ascii=False), now),
        )
        self.conn.commit()

    def list_belief_models(self, scopes: list[str] | None = None) -> list[dict]:
        """Return all belief_models matching scopes, newest-updated first."""
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"SELECT domain, scope, latest, entries, summary, trajectory, signals, last_consolidated, dirty, updated FROM belief_models "
                f"WHERE scope IN ({placeholders}) ORDER BY updated DESC",
                scopes,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT domain, scope, latest, entries, summary, trajectory, signals, last_consolidated, dirty, updated FROM belief_models ORDER BY updated DESC"
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "domain": r["domain"],
                "scope": r["scope"],
                "latest": r["latest"],
                "entries": json.loads(r["entries"]),
                "summary": r["summary"] or "",
                "trajectory": r["trajectory"] or "",
                "signals": json.loads(r["signals"] or "[]"),
                "last_consolidated": int(r["last_consolidated"] or 0),
                "dirty": int(r["dirty"] or 0),
                "updated": r["updated"],
            })
        return result

    def list_dirty_belief_models(
        self,
        *,
        scopes: list[str] | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """Return dirty belief models, newest first, for async consolidation."""
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"""SELECT domain, scope, latest, entries, summary, trajectory, signals,
                           last_consolidated, dirty, updated
                    FROM belief_models
                    WHERE dirty=1 AND scope IN ({placeholders})
                    ORDER BY updated DESC
                    LIMIT ?""",
                (*scopes, max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT domain, scope, latest, entries, summary, trajectory, signals,
                          last_consolidated, dirty, updated
                   FROM belief_models
                   WHERE dirty=1
                   ORDER BY updated DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "domain": r["domain"],
                "scope": r["scope"],
                "latest": r["latest"] or "",
                "entries": json.loads(r["entries"] or "[]"),
                "summary": r["summary"] or "",
                "trajectory": r["trajectory"] or "",
                "signals": json.loads(r["signals"] or "[]"),
                "last_consolidated": int(r["last_consolidated"] or 0),
                "dirty": int(r["dirty"] or 0),
                "updated": int(r["updated"] or 0),
            })
        return out

    def save_belief_model_consolidation(
        self,
        *,
        domain: str,
        scope: str,
        summary: str,
        trajectory: str,
        signals: list[str],
    ) -> None:
        """Persist async model-powered consolidation results and clear dirty flag."""
        now = int(time.time())
        clean_signals = [str(s).strip()[:120] for s in signals if str(s).strip()]
        self.conn.execute(
            """UPDATE belief_models
               SET summary=?, trajectory=?, signals=?, last_consolidated=?, dirty=0
               WHERE domain=? AND scope=?""",
            (
                summary.strip()[:220],
                trajectory.strip()[:220],
                json.dumps(clean_signals[:3], ensure_ascii=False),
                now,
                domain,
                scope,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _belief_query_terms(query: str) -> set[str]:
        """Extract lightweight lowercase query terms for domain matching."""
        if not query:
            return set()
        terms = {
            t.lower()
            for t in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)
            if len(t.strip()) >= 2
        }
        return terms

    @classmethod
    def _score_belief_model(cls, model: dict, query: str) -> float:
        """Return a precision-focused routing score for one belief model.

        Routing signal priority (high → low):
          1. domain name parts (split compound names like AI/LLM)
          2. signals — LLM-extracted key phrases
          3. summary — LLM-synthesised stance

        Raw entry text and latest content are intentionally excluded to avoid
        false positives from incidental keyword overlap.
        """
        terms = cls._belief_query_terms(query)
        # No query → pure timestamp so caller can rank by recency.
        if not terms:
            return float(model.get("updated") or 0)

        score = 0.0

        # 1. Domain name — split compound domains (e.g. "AI/LLM" → {ai, llm})
        raw_domain = str(model.get("domain") or "").lower()
        domain_parts = set(re.split(r"[/\-_\s]+", raw_domain)) - {""}
        for part in domain_parts:
            if part in terms:
                score += 6.0           # exact part match
            elif any(part in t or t in part for t in terms):
                score += 3.0           # substring match
                break

        # 2. Signals — LLM-refined key phrases (highest-precision route signal)
        raw_signals = model.get("signals") or []
        if isinstance(raw_signals, str):
            try:
                raw_signals = json.loads(raw_signals)
            except Exception:
                raw_signals = []
        signal_text = " ".join(str(s) for s in raw_signals).lower()
        signal_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", signal_text))
        score += len(terms & signal_terms) * 3.0

        # 3. Summary — one-sentence LLM stance (secondary route signal)
        summary = str(model.get("summary") or "").lower()
        if summary:
            sum_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", summary))
            score += len(terms & sum_terms) * 1.5

        # Tiny timestamp tiebreaker — never changes domain ranking, only breaks ties.
        score += min(float(model.get("updated") or 0) / 1_000_000_000_000, 0.5)
        return score

    @staticmethod
    def _summarize_belief_evolution(entries: list[dict]) -> tuple[str, str]:
        """Return (history_line, trajectory_line) for prompt injection."""
        if len(entries) <= 1:
            note_type = str(entries[0].get("note_type") or "") if entries else ""
            if note_type == "interest":
                return "", "→ Signal: active interest observed, but no prior evolution yet"
            return "", ""

        previous = [str(e.get("content") or "").strip() for e in entries[1:4] if str(e.get("content") or "").strip()]
        note_types = [str(e.get("note_type") or "") for e in entries[:4]]
        unique_previous = []
        for item in previous:
            if item not in unique_previous:
                unique_previous.append(item)

        history_line = ""
        if unique_previous:
            history_line = "→ Before: " + " | ".join(item[:80] for item in unique_previous[:2])

        belief_count = sum(1 for t in note_types if t == "belief")
        interest_count = sum(1 for t in note_types if t == "interest")
        if belief_count and interest_count:
            trajectory = "→ Trajectory: mixes stable viewpoints with repeated curiosity in this domain"
        elif interest_count >= 2:
            trajectory = "→ Trajectory: recurring exploration signal across multiple turns"
        elif belief_count >= 2:
            trajectory = "→ Trajectory: repeated judgment pattern, suggesting an emerging stable stance"
        else:
            trajectory = "→ Trajectory: early signal only; model is still sparse"
        return history_line, trajectory

    # Minimum routing score required to inject a belief model.
    # With an active query, domains scoring below this threshold are skipped entirely.
    _BELIEF_ROUTE_MIN_SCORE: float = 3.0

    def render_belief_models(
        self,
        scopes: list[str] | None = None,
        *,
        query: str = "",
        max_chars: int = 700,
        max_models: int = 3,
    ) -> str:
        """Format belief models for prompt injection. Query-aware, compact, and scoped."""
        models = self.list_belief_models(scopes=scopes)
        if not models:
            return ""

        has_query = bool(query.strip())
        # Without a query, show only the single most recently updated domain
        # to avoid flooding context with unrelated beliefs.
        effective_max = max_models if has_query else 1

        ranked = sorted(
            models,
            key=lambda m: self._score_belief_model(m, query),
            reverse=True,
        )

        lines: list[str] = []
        char_budget = max_chars
        selected = 0
        for m in ranked:
            if selected >= effective_max:
                break
            # Apply min-score gate when there is an active query.
            # ranked is descending, so once we fall below threshold all remaining will too.
            if has_query and self._score_belief_model(m, query) < self._BELIEF_ROUTE_MIN_SCORE:
                break
            entries = m["entries"]
            count = len(entries)
            if count == 0:
                continue
            from datetime import datetime, timezone
            date_str = datetime.fromtimestamp(m["updated"], tz=timezone.utc).strftime("%Y-%m-%d")
            header = f"**{m['domain']}** ({count} belief{'s' if count != 1 else ''}, updated {date_str})"
            latest = m["latest"][:120]
            line = f"{header}\n→ Current: {latest}"
            summary = str(m.get("summary") or "").strip()
            trajectory = str(m.get("trajectory") or "").strip()
            signals = [str(s).strip() for s in (m.get("signals") or []) if str(s).strip()]
            history_line, fallback_trajectory = self._summarize_belief_evolution(entries)
            if summary:
                line += f"\n→ Model: {summary[:160]}"
            if history_line:
                line += f"\n{history_line}"
            if trajectory:
                line += f"\n→ Trajectory: {trajectory[:160]}"
            elif fallback_trajectory:
                line += f"\n{fallback_trajectory}"
            if signals:
                line += "\n→ Signals: " + " | ".join(s[:60] for s in signals[:2])
            if char_budget - len(line) < 0:
                break
            lines.append(line)
            char_budget -= len(line)
            selected += 1
        return "\n\n".join(lines)

    def search_by_tag(self, tag: str, limit: int = 10) -> list[dict]:
        """Return notes that carry the given tag (exact match in JSON array)."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.recall_count, n.memory_kind, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? LIMIT ?",
            (tag, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_recall_count(self, note_id: str) -> None:
        """Increment a note's recall_count for lightweight retrieval telemetry."""
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
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes with their bodies."""
        clauses: list[str] = []
        params: list[object] = []
        if exclude_tags:
            ph = ",".join("?" * len(exclude_tags))
            clauses.append(
                f"NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({ph}))"
            )
            params.extend(exclude_tags)
        if include_kinds:
            ph = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({ph})")
            params.extend(sorted(include_kinds))
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT n.note_id, n.title, n.tags, n.note_type, n.memory_kind, b.body FROM notes n "
            f"LEFT JOIN note_bodies b USING(note_id) "
            f"{where_sql}"
            f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
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
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes whose scope is in `scopes`."""
        scope_ph = ",".join("?" * len(scopes))
        clauses = [f"n.scope IN ({scope_ph})"]
        params: list[object] = list(scopes)
        if exclude_tags:
            tag_ph = ",".join("?" * len(exclude_tags))
            clauses.append(
                f"NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({tag_ph}))"
            )
            params.extend(exclude_tags)
        if include_kinds:
            kind_ph = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({kind_ph})")
            params.extend(sorted(include_kinds))
        rows = self.conn.execute(
            f"SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.memory_kind, b.body FROM notes n "
            f"LEFT JOIN note_bodies b USING(note_id) "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------

    def _fetch_note_metadata(self, note_ids: list[str]) -> dict[str, tuple[int, str, str]]:
        if not note_ids:
            return {}
        placeholders = ",".join("?" * len(note_ids))
        rows = self.conn.execute(
            f"SELECT note_id, recall_count, note_type, memory_kind FROM notes WHERE note_id IN ({placeholders})",
            note_ids,
        ).fetchall()
        return {
            row["note_id"]: (
                int(row["recall_count"] or 0),
                row["note_type"] or "fact",
                row["memory_kind"] or "project_knowledge",
            )
            for row in rows
        }

    def search(
        self,
        query: str,
        limit: int = 5,
        include_kinds: set[str] | None = None,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Hybrid FTS + vector search, merged by score."""
        visible_kinds = include_kinds if include_kinds is not None else USER_VISIBLE_MEMORY_KINDS
        blocked_tags = list(dict.fromkeys((exclude_tags or []) + sorted(SYSTEM_MEMORY_TAGS)))
        fts_results = {r["note_id"]: r for r in self._fts.search(query, limit * 2, exclude_tags=blocked_tags)}
        vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2, exclude_tags=blocked_tags)}

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

        meta = self._fetch_note_metadata([r["note_id"] for r in merged])
        filtered = []
        for r in merged:
            _rc, note_type, memory_kind = meta.get(r["note_id"], (0, "fact", "project_knowledge"))
            if visible_kinds and memory_kind not in visible_kinds:
                continue
            r["note_type"] = note_type
            r["memory_kind"] = memory_kind
            filtered.append(r)

        filtered.sort(key=lambda x: x["score"], reverse=True)
        return filtered[:limit]

    def recall(self, query: str, limit: int = 5) -> str:
        """Return a formatted string of top search results for LLM injection."""
        results = self.search(query, limit, include_kinds=RECALL_MEMORY_KINDS)
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
        include_kinds: set[str] | None = None,
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
                     tuple(sorted(exclude_types)) if exclude_types else None,
                     tuple(sorted(include_kinds)) if include_kinds else None)
        cached = self._recall_cache.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        # Internal system notes are never surfaced as recalled memories.
        # _compact_archive: raw conversation dumps (huge, noisy).
        _exclude = sorted(SYSTEM_MEMORY_TAGS)
        recall_kinds = include_kinds if include_kinds is not None else RECALL_MEMORY_KINDS

        # Empty query = serendipity random sampling from all notes
        if not query.strip():
            merged = self._random_sample_notes(limit * 2, scopes=scopes, include_kinds=recall_kinds)
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

        # recall_count boost and note_type/kind filter/boost — single batch DB query.
        _TYPE_BOOST = {"interest": 1.10, "belief": 1.10, "preference": 1.10}
        if merged:
            rc_map = self._fetch_note_metadata([r["note_id"] for r in merged])
            kept_after_type = []
            for r in merged:
                rc, note_type, memory_kind = rc_map.get(r["note_id"], (0, "fact", "project_knowledge"))
                # Exclude blocked types (e.g. action_log)
                if exclude_types and note_type in exclude_types:
                    continue
                if recall_kinds and memory_kind not in recall_kinds:
                    continue
                if rc > 0:
                    r["score"] = r["score"] * (1.0 + 0.1 * math.log1p(rc))
                # Boost user-modeling types
                type_mult = _TYPE_BOOST.get(note_type, 1.0)
                if type_mult != 1.0:
                    r["score"] = r["score"] * type_mult
                r["note_type"] = note_type
                r["memory_kind"] = memory_kind
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

    def _random_sample_notes(
        self,
        limit: int,
        scopes: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return random notes for serendipity injection. All get score=1.0."""
        clauses: list[str] = []
        params: list[object] = []
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            clauses.append(f"n.scope IN ({placeholders})")
            params.extend(scopes)
        if include_kinds:
            placeholders = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({placeholders})")
            params.extend(sorted(include_kinds))
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT n.note_id, n.title, n.created, n.note_type, n.memory_kind, b.body "
            f"FROM notes n JOIN note_bodies b USING(note_id) "
            f"{where_sql}"
            f"ORDER BY RANDOM() LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [
            {
                "note_id": r["note_id"],
                "title": r["title"] or "",
                "body": r["body"] or "",
                "created": r["created"],
                "note_type": r["note_type"] or "fact",
                "memory_kind": r["memory_kind"] or "project_knowledge",
                "score": 1.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Session / Turn persistence
    # ------------------------------------------------------------------

    def _backfill_sessions(self) -> None:
        """Populate session metadata rows for DBs created before session tracking."""
        rows = self.conn.execute(
            "SELECT session, MIN(ts) AS created, MAX(ts) AS last_turn, COUNT(*) AS turn_count, "
            "MAX(workspace) AS workspace "
            "FROM turns GROUP BY session"
        ).fetchall()
        for row in rows:
            session_id = str(row["session"] or "")
            if not session_id:
                continue
            kind = self._session_kind(session_id)
            title = self._first_user_title(session_id) or self._fallback_title(session_id)
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, kind, title, workspace, created, updated, last_turn, turn_count) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    kind,
                    title,
                    str(row["workspace"] or ""),
                    int(row["created"] or int(time.time())),
                    int(row["last_turn"] or int(time.time())),
                    int(row["last_turn"] or 0),
                    int(row["turn_count"] or 0),
                ),
            )
        self.conn.commit()

    def _backfill_turns_fts(self) -> None:
        """Index persisted turns for cross-session full-text search."""
        self.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) "
            "SELECT rowid, turn_id, session, role, content FROM turns "
            "WHERE turn_id NOT IN (SELECT turn_id FROM turns_fts)"
        )
        self.conn.commit()

    def _first_user_title(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM turns WHERE session=? AND role='user' ORDER BY ts ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return ""
        return self._clip_text(str(row["content"] or ""), 56)

    def _upsert_session(
        self,
        session_id: str,
        *,
        created: int | None = None,
        updated: int | None = None,
        last_turn: int | None = None,
        workspace: str = "",
        source: str = "",
        parent_session_id: str = "",
        title: str = "",
    ) -> None:
        now = int(time.time())
        created_ts = created if created is not None else now
        updated_ts = updated if updated is not None else created_ts
        last_turn_ts = last_turn if last_turn is not None else updated_ts
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, parent_session_id, source, kind, title, workspace, created, updated, last_turn, turn_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                session_id,
                parent_session_id or "",
                source or "",
                self._session_kind(session_id),
                title or self._fallback_title(session_id),
                workspace or "",
                created_ts,
                updated_ts,
                last_turn_ts,
            ),
        )

    def annotate_session(
        self,
        session_id: str,
        *,
        source: str = "",
        workspace: str = "",
        parent_session_id: str = "",
        title: str = "",
    ) -> None:
        """Update session metadata that may arrive outside raw turn persistence."""
        self._upsert_session(
            session_id,
            workspace=workspace,
            source=source,
            parent_session_id=parent_session_id,
            title=title or self._fallback_title(session_id),
        )
        row = self.conn.execute(
            "SELECT title, source, workspace, parent_session_id FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "UPDATE sessions SET source=?, workspace=?, parent_session_id=?, title=?, updated=? "
            "WHERE session_id=?",
            (
                source or str(row["source"] or ""),
                workspace or str(row["workspace"] or ""),
                parent_session_id or str(row["parent_session_id"] or ""),
                title or str(row["title"] or "") or self._fallback_title(session_id),
                int(time.time()),
                session_id,
            ),
        )
        self.conn.commit()

    def record_session_compaction(
        self,
        session_id: str,
        *,
        archived: int,
        kept: int,
        parent_session_id: str = "",
    ) -> str:
        """Persist a lineage event for a compaction boundary."""
        now = int(time.time())
        self._upsert_session(session_id, updated=now, last_turn=now)
        lineage_id = make_id("lin-")
        summary_path = str((self.sessions_dir / session_id / "summary.md").resolve())
        meta = {
            "archived": archived,
            "kept": kept,
            "summary_path": summary_path,
        }
        self.conn.execute(
            "INSERT INTO session_lineage (lineage_id, session_id, parent_session_id, relationship, ts, meta_json) "
            "VALUES (?,?,?,?,?,?)",
            (
                lineage_id,
                session_id,
                parent_session_id or session_id,
                "compacted",
                now,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            "UPDATE sessions SET compaction_count=compaction_count+1, "
            "last_compacted_at=?, updated=? WHERE session_id=?",
            (now, now, session_id),
        )
        self.conn.commit()
        return lineage_id

    def get_session_lineage(self, session_id: str) -> list[dict]:
        """Return lineage events for a session, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM session_lineage WHERE session_id=? ORDER BY ts DESC",
            (session_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["meta_json"] = json.loads(item.get("meta_json") or "{}")
            except Exception:
                item["meta_json"] = {}
            out.append(item)
        return out

    def search_sessions(
        self,
        query: str,
        limit: int = 20,
        include_scheduled: bool = True,
        workspace: str | None = None,
    ) -> list[dict]:
        """Search persisted conversation turns across sessions."""
        q = (query or "").strip()
        if not q:
            return []
        where = ["turns_fts MATCH ?"]
        params: list[object] = [q]
        if not include_scheduled:
            where.append("t.session NOT LIKE 'sched_%'")
        if workspace is not None:
            where.append("COALESCE(s.workspace, '') = ?")
            params.append(workspace)
        sql = (
            "SELECT t.session AS session_id, t.turn_id, t.role, t.content, t.ts, "
            "bm25(turns_fts) AS score, "
            "snippet(turns_fts, 3, '[', ']', '…', 12) AS snippet, "
            "COALESCE(s.title, '') AS title, COALESCE(s.kind, '') AS kind, "
            "COALESCE(s.parent_session_id, '') AS parent_session_id, "
            "COALESCE(s.source, '') AS source, COALESCE(s.compaction_count, 0) AS compaction_count "
            "FROM turns_fts "
            "JOIN turns t ON t.rowid = turns_fts.rowid "
            "LEFT JOIN sessions s ON s.session_id = t.session "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY score, t.ts DESC LIMIT ?"
        )
        params.append(max(1, int(limit)))
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

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
        self._upsert_session(
            session_id,
            created=now,
            updated=now,
            last_turn=now,
            workspace=workspace or "",
            title=self._fallback_title(session_id),
        )
        self.conn.execute(
            "INSERT INTO turns (turn_id, session, role, content, tool_name, ts, "
            "input_tokens, output_tokens, workspace) VALUES (?,?,?,?,?,?,?,?,?)",
            (turn_id, session_id, role, content, tool_name, now, input_tokens, output_tokens, workspace or ""),
        )
        self.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) VALUES (last_insert_rowid(), ?, ?, ?, ?)",
            (turn_id, session_id, role, content),
        )
        current_title = self._clip_text(content, 56) if role == "user" else ""
        self.conn.execute(
            "UPDATE sessions SET updated=?, last_turn=?, turn_count=turn_count+1, "
            "workspace=CASE WHEN ? != '' THEN ? ELSE workspace END, "
            "title=CASE WHEN title='' AND ? != '' THEN ? ELSE title END "
            "WHERE session_id=?",
            (
                now,
                now,
                workspace or "",
                workspace or "",
                current_title,
                current_title,
                session_id,
            ),
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
        offset: int = 0,
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
            where.append("COALESCE(s.workspace, '') = ?")
            params.append(workspace)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT s.session_id AS session_id, s.parent_session_id AS parent_session_id, "
            "s.source AS source, s.workspace AS workspace, "
            "COALESCE(NULLIF(s.kind, ''), 'chat') AS kind, "
            "s.compaction_count AS compaction_count, s.last_compacted_at AS last_compacted_at, "
            "s.turn_count AS turn_count, s.created AS started, s.last_turn AS last_turn, "
            "SUM(t.input_tokens) AS total_input_tokens, SUM(t.output_tokens) AS total_output_tokens, "
            "(SELECT tu.content FROM turns tu WHERE tu.session=s.session_id AND tu.role='user' ORDER BY tu.ts ASC LIMIT 1) AS first_user_text, "
            "(SELECT tl.content FROM turns tl WHERE tl.session=s.session_id ORDER BY tl.ts DESC LIMIT 1) AS last_content, "
            "s.title AS stored_title "
            f"FROM sessions s LEFT JOIN turns t ON t.session=s.session_id {where_sql} "
            "GROUP BY s.session_id ORDER BY s.last_turn DESC LIMIT ? OFFSET ?"
        )
        params.append(max(1, int(limit)))
        params.append(max(0, int(offset)))
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
                title = str(d.get("stored_title") or "")
            if not title:
                title = self._clip_text(first_user, 56) or self._fallback_title(session_id)

            d["title"] = title
            d["last_preview"] = self._clip_text(last_content, 72)
            d["kind"] = kind
            d.pop("first_user_text", None)
            d.pop("last_content", None)
            d.pop("stored_title", None)
            out.append(d)
        return out

    def delete_session(self, session_id: str) -> bool:
        """Delete all turns for a session from the DB and its summary file."""
        self.conn.execute("DELETE FROM turns_fts WHERE session=?", (session_id,))
        self.conn.execute("DELETE FROM turns WHERE session=?", (session_id,))
        self.conn.execute("DELETE FROM session_lineage WHERE session_id=?", (session_id,))
        self.conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
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

    def save_session_working_state(self, session_id: str, state_text: str) -> None:
        """Persist a compact working-state checkpoint for a session."""
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "working_state.md").write_text(state_text, encoding="utf-8")

    def load_session_working_state(self, session_id: str) -> str | None:
        """Load the compact working-state checkpoint for a session."""
        p = self.sessions_dir / session_id / "working_state.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def record_reflection(
        self,
        *,
        session_id: str,
        task_fingerprint: str,
        success: bool,
        outcome: str,
        failure_mode: str = "",
        lesson: str = "",
        strategy_hint: str = "",
        skill_name: str = "",
        source_turn_count: int = 0,
    ) -> str:
        reflection_id = make_id("refl-")
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO reflections "
            "(reflection_id, session_id, task_fingerprint, success, outcome, failure_mode, lesson, strategy_hint, skill_name, source_turn_count, created) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                reflection_id,
                session_id,
                task_fingerprint or "general_assistance",
                1 if success else 0,
                outcome or "",
                failure_mode or "",
                lesson or "",
                strategy_hint or "",
                skill_name or "",
                max(0, int(source_turn_count)),
                now,
            ),
        )
        self.conn.commit()
        return reflection_id

    def list_reflections(
        self,
        *,
        task_fingerprint: str = "",
        limit: int = 20,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if task_fingerprint:
            clauses.append("task_fingerprint=?")
            params.append(task_fingerprint)
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM reflections {where_sql} ORDER BY created DESC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_skill_outcome(
        self,
        *,
        skill_name: str,
        session_id: str,
        task_fingerprint: str = "",
        success: bool,
        note: str = "",
        quality_score: float = 1.0,
    ) -> str:
        outcome_id = make_id("sko-")
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO skill_outcomes "
            "(outcome_id, skill_name, session_id, task_fingerprint, success, note, quality_score, created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                outcome_id,
                skill_name,
                session_id,
                task_fingerprint or "",
                1 if success else 0,
                note or "",
                max(0.0, min(1.0, float(quality_score))),
                now,
            ),
        )
        self.conn.commit()
        return outcome_id

    def list_skill_outcomes(self, skill_name: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skill_outcomes WHERE skill_name=? ORDER BY created DESC LIMIT ?",
            (skill_name, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_skill_outcomes(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skill_outcomes ORDER BY created DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]

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

    # ------------------------------------------------------------------ calendar

    def add_calendar_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        color: str = "indigo",
        all_day: bool = False,
        attendees: list[str] | None = None,
        source: str = "local",
    ) -> dict:
        import time as _time
        event_id = "evt-" + make_id()
        now = int(_time.time())
        attendees_json = json.dumps(attendees or [])
        self.conn.execute(
            "INSERT INTO calendar_events "
            "(event_id, title, description, location, start_time, end_time, all_day, color, attendees, source, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, title, description, location, start_time, end_time, int(all_day), color, attendees_json, source, now, now),
        )
        self.conn.commit()
        return self.get_calendar_event(event_id)

    def upsert_caldav_event(
        self,
        event_id: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        remote_uid: str = "",
        remote_href: str = "",
        remote_etag: str = "",
        recurrence_id: str = "",
        remote_calendar: str = "",
    ) -> int:
        """Insert or update a CalDAV-sourced event. Never overwrites source='local' rows.

        Returns 1 if the row was inserted or updated, 0 if skipped
        (e.g. a local event already owns that event_id).
        """
        import time as _time
        now = int(_time.time())
        cur = self.conn.execute(
            """
            INSERT INTO calendar_events
                (event_id, title, description, location, start_time, end_time,
                 all_day, color, attendees, source, remote_uid, remote_href,
                 remote_etag, recurrence_id, remote_calendar, last_seen_at,
                 created, updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                location=excluded.location,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                all_day=excluded.all_day,
                source='caldav',
                remote_uid=excluded.remote_uid,
                remote_href=excluded.remote_href,
                remote_etag=excluded.remote_etag,
                recurrence_id=excluded.recurrence_id,
                remote_calendar=excluded.remote_calendar,
                last_seen_at=excluded.last_seen_at,
                updated=excluded.updated
            WHERE source='caldav'
            """,
            (
                event_id,
                title,
                description,
                location,
                start_time,
                end_time,
                int(all_day),
                "indigo",
                "[]",
                "caldav",
                remote_uid,
                remote_href,
                remote_etag,
                recurrence_id,
                remote_calendar,
                now,
                now,
                now,
            ),
        )
        self.conn.commit()
        return cur.rowcount  # 1 = inserted or updated; 0 = skipped (source='local')

    def clear_caldav_events(self) -> int:
        """Delete ALL source='caldav' events. Used before a full re-sync."""
        cur = self.conn.execute("DELETE FROM calendar_events WHERE source='caldav'")
        self.conn.commit()
        return cur.rowcount

    def prune_stale_caldav_events(self, kept_ids: set) -> int:
        """Delete source='caldav' rows whose event_id is not in kept_ids.

        Called after a full CalDAV pull to remove events deleted on the server.
        Returns the number of rows deleted.
        """
        if not kept_ids:
            # Safety: if the pull returned nothing, don't wipe everything.
            return 0
        placeholders = ",".join("?" * len(kept_ids))
        cur = self.conn.execute(
            f"DELETE FROM calendar_events WHERE source='caldav' AND event_id NOT IN ({placeholders})",
            list(kept_ids),
        )
        self.conn.commit()
        return cur.rowcount

    def get_caldav_event_ids_for_resource(
        self,
        *,
        remote_href: str = "",
        remote_uid: str = "",
        remote_etag: str = "",
        remote_calendar: str = "",
    ) -> list[str]:
        """Return existing CalDAV event IDs for one remote resource.

        Used to short-circuit unchanged non-recurring resources when the
        remote ETag matches what we already have locally.
        """
        if remote_href:
            where = ["source='caldav'", "remote_href=?"]
            params: list[str] = [remote_href]
            if remote_etag:
                where.append("remote_etag=?")
                params.append(remote_etag)
            rows = self.conn.execute(
                f"SELECT event_id FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            return [str(r[0]) for r in rows]
        if remote_uid:
            where = ["source='caldav'", "remote_uid=?"]
            params = [remote_uid]
            if remote_calendar:
                where.append("remote_calendar=?")
                params.append(remote_calendar)
            if remote_etag:
                where.append("remote_etag=?")
                params.append(remote_etag)
            rows = self.conn.execute(
                f"SELECT event_id FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            return [str(r[0]) for r in rows]
        return []

    def get_caldav_event_ids_for_calendar(self, remote_calendar: str) -> list[str]:
        if not remote_calendar:
            return []
        rows = self.conn.execute(
            "SELECT event_id FROM calendar_events WHERE source='caldav' AND remote_calendar=?",
            (remote_calendar,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def delete_caldav_events_by_resource(
        self,
        *,
        remote_href: str = "",
        remote_uid: str = "",
        remote_calendar: str = "",
    ) -> int:
        if remote_href:
            cur = self.conn.execute(
                "DELETE FROM calendar_events WHERE source='caldav' AND remote_href=?",
                (remote_href,),
            )
            self.conn.commit()
            return cur.rowcount
        if remote_uid:
            where = ["source='caldav'", "remote_uid=?"]
            params: list[str] = [remote_uid]
            if remote_calendar:
                where.append("remote_calendar=?")
                params.append(remote_calendar)
            cur = self.conn.execute(
                f"DELETE FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            )
            self.conn.commit()
            return cur.rowcount
        return 0

    def touch_caldav_events_seen(self, event_ids: list[str] | set[str]) -> int:
        """Refresh last_seen_at for existing CalDAV rows reused via ETag short-circuit."""
        ids = [str(eid) for eid in event_ids if eid]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        now = int(time.time())
        cur = self.conn.execute(
            f"UPDATE calendar_events SET last_seen_at=? WHERE source='caldav' AND event_id IN ({placeholders})",
            [now] + ids,
        )
        self.conn.commit()
        return cur.rowcount

    def get_caldav_collection_state(self, collection_key: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT collection_key, last_ctag, last_sync_token, last_scan_at, last_result_count, updated
            FROM caldav_collection_state
            WHERE collection_key=?
            """,
            (collection_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_caldav_collection_state(
        self,
        collection_key: str,
        *,
        last_ctag: str,
        last_sync_token: str,
        last_scan_at: int,
        last_result_count: int,
    ) -> dict:
        updated = int(time.time())
        self.conn.execute(
            """
            INSERT INTO caldav_collection_state
                (collection_key, last_ctag, last_sync_token, last_scan_at, last_result_count, updated)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(collection_key) DO UPDATE SET
                last_ctag=excluded.last_ctag,
                last_sync_token=excluded.last_sync_token,
                last_scan_at=excluded.last_scan_at,
                last_result_count=excluded.last_result_count,
                updated=excluded.updated
            """,
            (
                str(collection_key or "")[:1000],
                str(last_ctag or "")[:1000],
                str(last_sync_token or "")[:4000],
                max(0, int(last_scan_at)),
                max(0, int(last_result_count)),
                updated,
            ),
        )
        self.conn.commit()
        return self.get_caldav_collection_state(collection_key) or {}

    def get_caldav_sync_state(self, sync_key: str = "default") -> dict | None:
        row = self.conn.execute(
            """
            SELECT sync_key, last_attempt, last_success, last_failure,
                   failure_count, last_error, last_result_count, updated
            FROM caldav_sync_state
            WHERE sync_key=?
            """,
            (sync_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_caldav_sync_state(
        self,
        sync_key: str,
        *,
        last_attempt: int,
        last_success: int,
        last_failure: int,
        failure_count: int,
        last_error: str,
        last_result_count: int,
    ) -> dict:
        updated = int(time.time())
        self.conn.execute(
            """
            INSERT INTO caldav_sync_state
                (sync_key, last_attempt, last_success, last_failure,
                 failure_count, last_error, last_result_count, updated)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(sync_key) DO UPDATE SET
                last_attempt=excluded.last_attempt,
                last_success=excluded.last_success,
                last_failure=excluded.last_failure,
                failure_count=excluded.failure_count,
                last_error=excluded.last_error,
                last_result_count=excluded.last_result_count,
                updated=excluded.updated
            """,
            (
                sync_key,
                max(0, int(last_attempt)),
                max(0, int(last_success)),
                max(0, int(last_failure)),
                max(0, int(failure_count)),
                str(last_error or "")[:1000],
                max(0, int(last_result_count)),
                updated,
            ),
        )
        self.conn.commit()
        return self.get_caldav_sync_state(sync_key) or {}

    def get_calendar_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM calendar_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["attendees"] = json.loads(d.get("attendees") or "[]")
        d["all_day"] = bool(d.get("all_day", 0))
        return d

    def list_calendar_events(
        self,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> list[dict]:
        # Interval overlap: event overlaps [from, to) when start < to AND end > from.
        # This correctly captures ongoing events that started before the query window.
        if from_time and to_time:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events WHERE start_time < ? AND end_time > ? "
                "ORDER BY start_time ASC",
                (to_time, from_time),
            ).fetchall()
        elif from_time:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events WHERE end_time > ? ORDER BY start_time ASC",
                (from_time,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events ORDER BY start_time ASC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["attendees"] = json.loads(d.get("attendees") or "[]")
            d["all_day"] = bool(d.get("all_day", 0))
            result.append(d)
        return result

    def update_calendar_event(self, event_id: str, **fields) -> dict | None:
        import time as _time
        allowed = {"title", "description", "location", "start_time", "end_time", "all_day", "color", "attendees"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_calendar_event(event_id)
        if "attendees" in updates and isinstance(updates["attendees"], list):
            updates["attendees"] = json.dumps(updates["attendees"])
        if "all_day" in updates:
            updates["all_day"] = int(bool(updates["all_day"]))
        updates["updated"] = int(_time.time())
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [event_id]
        self.conn.execute(
            f"UPDATE calendar_events SET {set_clause} WHERE event_id=?", values
        )
        self.conn.commit()
        return self.get_calendar_event(event_id)

    def delete_calendar_event(self, event_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM calendar_events WHERE event_id=?", (event_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------
    # Knowledge base: document ingestion
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count: ~4 chars per token (BPE approximation)."""
        return max(1, len(text) // 4)

    @staticmethod
    def _hash_content(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

    def ingest_file(
        self,
        file_path: "Path | str",
        scope: str = "global",
        chunk_size: int = 512,
        overlap: int = 64,
        tags: list[str] | None = None,
    ) -> dict:
        """Ingest a text file into the knowledge base as chunked notes.

        Splits the file into paragraph-level chunks (~chunk_size tokens each),
        saves each chunk as a 'document_chunk' note, and records provenance in
        document_sources. Re-ingesting an unchanged file is a no-op (returns
        skipped=True). Re-ingesting a changed file deletes old chunks first.

        Returns {"source_id": ..., "chunk_count": int, "skipped": bool}.
        """
        file_path = Path(file_path).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(str(file_path))

        try:
            raw = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise OSError(f"Cannot read {file_path}: {e}") from e

        content_hash = self._hash_content(raw)

        # Check existing source record
        existing = self.conn.execute(
            "SELECT source_id, file_hash FROM document_sources WHERE file_path=?",
            (str(file_path),),
        ).fetchone()

        if existing and existing["file_hash"] == content_hash:
            return {
                "source_id": existing["source_id"],
                "chunk_count": self.conn.execute(
                    "SELECT chunk_count FROM document_sources WHERE source_id=?",
                    (existing["source_id"],),
                ).fetchone()["chunk_count"],
                "skipped": True,
            }

        # Delete old chunks if file changed
        if existing:
            self._delete_document_chunks(existing["source_id"])

        # Build chunks from paragraphs
        paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0
        overlap_carry = ""

        for para in paragraphs:
            para_tokens = self._estimate_tokens(para)
            if current_tokens + para_tokens > chunk_size and current_parts:
                chunk_text = overlap_carry + "\n\n".join(current_parts)
                chunks.append(chunk_text.strip())
                # Carry last paragraph as overlap into next chunk
                last = current_parts[-1]
                overlap_carry = (last + "\n\n") if self._estimate_tokens(last) <= overlap else ""
                current_parts = []
                current_tokens = 0
            current_parts.append(para)
            current_tokens += para_tokens

        if current_parts:
            chunks.append((overlap_carry + "\n\n".join(current_parts)).strip())

        if not chunks:
            chunks = [raw.strip()[:8000]]  # fallback for very short files

        # Create or update source record
        now = int(time.time())
        source_id = make_id()
        if existing:
            source_id = existing["source_id"]
            self.conn.execute(
                "UPDATE document_sources SET file_hash=?, chunk_count=?, scope=?, updated=? WHERE source_id=?",
                (content_hash, len(chunks), scope, now, source_id),
            )
        else:
            self.conn.execute(
                "INSERT INTO document_sources (source_id, file_path, import_time, file_hash, chunk_count, scope, updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (source_id, str(file_path), now, content_hash, len(chunks), scope, now),
            )
        self.conn.commit()

        # Save each chunk as a note
        file_stem = file_path.stem
        chunk_tags = list(tags or []) + ["_doc_chunk", f"source:{file_path.name}"]
        for i, chunk in enumerate(chunks):
            chunk_title = f"{file_stem} [{i + 1}/{len(chunks)}]"
            note_id = self._md.write_note(
                chunk,
                title=chunk_title,
                tags=chunk_tags,
                scope=scope,
                persist_to_disk=False,
                note_type="document_chunk",
                memory_kind="project_knowledge",
            )
            self.conn.execute(
                "UPDATE notes SET source_document_id=? WHERE note_id=?",
                (source_id, note_id),
            )
            self._vec.index(note_id, f"{chunk_title}\n{chunk}")
        self.conn.commit()

        return {"source_id": source_id, "chunk_count": len(chunks), "skipped": False}

    def _delete_document_chunks(self, source_id: str) -> int:
        """Delete all notes belonging to a document source. Returns count deleted."""
        rows = self.conn.execute(
            "SELECT note_id FROM notes WHERE source_document_id=?", (source_id,)
        ).fetchall()
        count = 0
        for row in rows:
            if self._md.delete_note(row["note_id"]):
                count += 1
        return count

    def list_document_sources(self, scope: str | None = None) -> list[dict]:
        """List all indexed document sources, newest first."""
        if scope:
            rows = self.conn.execute(
                "SELECT * FROM document_sources WHERE scope=? ORDER BY updated DESC",
                (scope,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM document_sources ORDER BY updated DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document_source_by_path(self, file_path: str) -> dict | None:
        """Return source record for a given file path, or None."""
        row = self.conn.execute(
            "SELECT * FROM document_sources WHERE file_path=?", (str(file_path),)
        ).fetchone()
        return dict(row) if row else None

    def delete_document_source(self, source_id: str) -> dict:
        """Remove a document source and all its chunks. Returns stats."""
        chunk_count = self._delete_document_chunks(source_id)
        self.conn.execute("DELETE FROM document_sources WHERE source_id=?", (source_id,))
        self.conn.commit()
        return {"deleted_chunks": chunk_count}
