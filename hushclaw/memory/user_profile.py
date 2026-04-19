"""Structured user profile storage for cross-session modeling."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any


class UserProfileStore:
    """Small façade over structured user profile facts in SQLite."""

    DEFAULT_MAX_FACTS_PER_CATEGORY = 4

    def __init__(self, conn) -> None:
        self.conn = conn

    def upsert_fact(
        self,
        *,
        category: str,
        key: str,
        value: dict[str, Any],
        confidence: float = 0.5,
        source_session_id: str = "",
    ) -> str:
        """Insert or update one structured profile fact."""
        now = int(time.time())
        row = self.conn.execute(
            "SELECT fact_id, confidence FROM user_profile_facts WHERE category=? AND key=?",
            (category, key),
        ).fetchone()
        if row is None:
            fact_id = f"upf-{now:x}-{abs(hash((category, key, now))) % 100000:05d}"
            self.conn.execute(
                "INSERT INTO user_profile_facts "
                "(fact_id, category, key, value_json, confidence, source_session_id, updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    fact_id,
                    category,
                    key,
                    json.dumps(value, ensure_ascii=False),
                    max(0.0, min(1.0, float(confidence))),
                    source_session_id or "",
                    now,
                ),
            )
        else:
            fact_id = str(row["fact_id"])
            old_conf = float(row["confidence"] or 0.0)
            new_conf = max(old_conf, max(0.0, min(1.0, float(confidence))))
            self.conn.execute(
                "UPDATE user_profile_facts SET value_json=?, confidence=?, source_session_id=?, updated=? "
                "WHERE fact_id=?",
                (
                    json.dumps(value, ensure_ascii=False),
                    new_conf,
                    source_session_id or "",
                    now,
                    fact_id,
                ),
            )
        self.conn.commit()
        return fact_id

    def list_facts(
        self,
        *,
        categories: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[object] = []
        if categories:
            placeholders = ",".join("?" * len(categories))
            clauses.append(f"category IN ({placeholders})")
            params.extend(categories)
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM user_profile_facts {where_sql} ORDER BY updated DESC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["value_json"] = json.loads(item.get("value_json") or "{}")
            except Exception:
                item["value_json"] = {}
            out.append(item)
        return out

    def get_profile_snapshot(
        self,
        *,
        max_facts_per_category: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        per_cat = max_facts_per_category or self.DEFAULT_MAX_FACTS_PER_CATEGORY
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self.list_facts(limit=200):
            cat = str(item.get("category") or "misc")
            if len(grouped[cat]) < per_cat:
                grouped[cat].append(item)
        return dict(grouped)

    def render_profile_context(self, *, max_chars: int = 1200) -> str:
        """Render a compact profile snapshot for prompt injection."""
        snapshot = self.get_profile_snapshot()
        if not snapshot:
            return ""
        label_map = {
            "communication_style": "Communication Style",
            "expertise": "Expertise & Capability",
            "avoidances": "Avoidances",
            "workflow_habits": "Workflow Habits",
            "tooling_preferences": "Tooling Preferences",
            "domains_of_interest": "Domains of Interest",
            "recurring_goals": "Recurring Goals",
            "preferences": "Preferences",
        }
        lines: list[str] = []
        for category in (
            "communication_style",
            "expertise",
            "avoidances",
            "workflow_habits",
            "tooling_preferences",
            "domains_of_interest",
            "recurring_goals",
            "preferences",
        ):
            items = snapshot.get(category) or []
            if not items:
                continue
            lines.append(f"### {label_map.get(category, category.replace('_', ' ').title())}")
            for item in items:
                value = item.get("value_json") or {}
                summary = value.get("summary") or value.get("value") or json.dumps(value, ensure_ascii=False)
                line = f"- {item.get('key')}: {summary}"
                lines.append(line[:220])
            lines.append("")
        text = "\n".join(lines).strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "…"
