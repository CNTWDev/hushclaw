"""One retrieval policy for the main context, tools, and read-only ports."""
from __future__ import annotations

import sqlite3

from hushclaw.memory.fts import FTSSearch
from hushclaw.memory.kinds import SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
from hushclaw.memory.vectors import VectorStore


CURRENT_NOTE_SQL = """n.status='active'
    AND NOT EXISTS (SELECT 1 FROM memory_feedback mf
                    WHERE mf.evidence_id='note:' || n.note_id AND mf.verdict='rejected')
    AND NOT EXISTS (SELECT 1 FROM message_states ms
                    WHERE ms.message_id=n.source_message_id
                      AND (ms.hidden=1 OR ms.excluded=1 OR ms.purged=1))
    AND (n.source_message_id='' OR
         (n.source_message_id LIKE 'turn:%' AND EXISTS (
             SELECT 1 FROM turns t WHERE t.turn_id=substr(n.source_message_id,6))) OR
         (n.source_message_id LIKE 'event:%' AND EXISTS (
             SELECT 1 FROM events e WHERE e.event_id=substr(n.source_message_id,7))) OR
         (n.source_message_id NOT LIKE 'turn:%' AND
          n.source_message_id NOT LIKE 'event:%'))"""


def memory_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM memory_meta WHERE key='revision'").fetchone()
    return int(row[0]) if row else 0


def visible_note_ids(conn: sqlite3.Connection, ids: list[str], *,
                     include_kinds: set[str] | None = None,
                     exclude_types: set[str] | None = None) -> dict[str, dict]:
    """Apply the same persistent visibility rules before any ranking or prompt use."""
    if not ids:
        return {}
    ph = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT n.note_id, n.note_type, n.memory_kind, n.scope,
                   n.recall_count, n.created, n.source_message_id
            FROM notes n
            WHERE n.note_id IN ({ph}) AND {CURRENT_NOTE_SQL}""",
        ids,
    ).fetchall()
    kinds = USER_VISIBLE_MEMORY_KINDS if include_kinds is None else include_kinds
    return {
        row["note_id"]: dict(row) for row in rows
        if (not kinds or row["memory_kind"] in kinds)
        and (not exclude_types or row["note_type"] not in exclude_types)
    }


def search_notes(conn: sqlite3.Connection, fts: FTSSearch, vec: VectorStore,
                 query: str, *, limit: int = 5, scopes: list[str] | None = None,
                 include_kinds: set[str] | None = None,
                 exclude_types: set[str] | None = None,
                 exclude_tags: list[str] | None = None,
                 fts_weight: float = 0.6, vec_weight: float = 0.4) -> list[dict]:
    """Hybrid candidate generation and RRF, with visibility enforced first."""
    if not query.strip() or limit <= 0 or scopes == []:
        return []
    blocked = sorted(SYSTEM_MEMORY_TAGS | set(exclude_tags or []))
    fetch_limit = max(20, limit * 4)
    lexical = fts.search(query, fetch_limit, scopes=scopes, exclude_tags=blocked)
    semantic = vec.search(query, fetch_limit, scopes=scopes, exclude_tags=blocked)
    if vec.embed_provider == "local":
        semantic = [r for r in semantic if r.get("score_vec", 0.0) >= 0.12]
    else:
        semantic = [r for r in semantic if r.get("score_vec", 0.0) >= 0.4]
    fts_map = {r["note_id"]: r for r in lexical}
    vec_map = {r["note_id"]: r for r in semantic}
    ids = list(dict.fromkeys([*fts_map, *vec_map]))
    visible = visible_note_ids(conn, ids, include_kinds=include_kinds,
                               exclude_types=exclude_types)
    fts_rank = {row["note_id"]: i for i, row in enumerate(lexical, 1)}
    vec_rank = {row["note_id"]: i for i, row in enumerate(semantic, 1)}
    results = []
    for note_id in ids:
        meta = visible.get(note_id)
        if meta is None:
            continue
        base = fts_map.get(note_id) or vec_map[note_id]
        score = 0.0
        if note_id in fts_rank:
            score += fts_weight * 61.0 / (60.0 + fts_rank[note_id])
        if note_id in vec_rank:
            score += vec_weight * 61.0 / (60.0 + vec_rank[note_id])
        results.append({
            "note_id": note_id, "title": base.get("title", ""),
            "body": base.get("body", ""), "tags": base.get("tags", []),
            "score": score, "score_fts": fts_map.get(note_id, {}).get("score_fts", 0.0),
            "score_vec": vec_map.get(note_id, {}).get("score_vec", 0.0),
            **meta,
        })
    results.sort(key=lambda item: (-item["score"], item["note_id"]))
    return results[:limit]
