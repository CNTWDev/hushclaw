"""server/memory_mixin.py — memory/note helper statics and compact_auto_memories.

Extracted from server_impl.py. All methods are accessed via self (mixin pattern).
"""
from __future__ import annotations

import json
import re
import time

from hushclaw.memory.kinds import ALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS


class MemoryMixin:
    """Mixin for HushClawServer: memory/note helpers and compact_auto_memories."""

    @staticmethod
    def _normalize_note_payload(item: dict) -> dict:
        """Normalize memory rows for WebUI rendering."""
        out = dict(item or {})
        created = out.get("created")
        modified = out.get("modified")
        out["created_at"] = int(created or modified or 0) if (created or modified) else 0
        if modified is not None:
            out["updated_at"] = int(modified)
        return out

    @staticmethod
    def _is_auto_extract_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_extract" in tags

    @staticmethod
    def _is_system_note(item: dict) -> bool:
        """True for internal system notes that should never appear in the user-facing memory list."""
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if bool(SYSTEM_MEMORY_TAGS & set(tags)):
            return True
        return item.get("memory_kind") in {"telemetry", "session_memory"}

    @staticmethod
    def _is_compacted_auto_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_compact" in tags

    @staticmethod
    def _normalize_memory_kind_filter(raw) -> set[str]:
        if raw is None or raw == "":
            return USER_VISIBLE_MEMORY_KINDS
        values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        values = [str(v).strip() for v in values if str(v).strip()]
        if any(v == "all" for v in values):
            return set(ALL_MEMORY_KINDS)
        normalized = {v for v in values if v in ALL_MEMORY_KINDS}
        return normalized or USER_VISIBLE_MEMORY_KINDS

    @staticmethod
    def _clean_auto_body(text: str) -> str:
        s = " ".join((text or "").split()).strip()
        s = re.sub(r"\*+", "", s)
        s = s.strip(" \t\r\n。，、,.;；:：\"'（）()[]【】「」『』-*_")
        return s

    @classmethod
    def _is_low_value_auto_note(cls, item: dict) -> bool:
        body = cls._clean_auto_body(item.get("body", ""))
        if not body:
            return True
        lower = body.lower()
        if any(p in body or p in lower for p in (
            "保存到记忆", "并保存到记忆", "已保存到记忆", "save to memory", "saved to memory",
        )):
            return True
        if len(body) < 8:
            return True
        if body.startswith(("并", "以及", "并且", "另外", "然后", "且", "and ", "then ")):
            return True
        if body.endswith((",", "，", ";", "；", ":", "：", '"', "'")):
            return True
        substantive = re.findall(r"[\w\u4e00-\u9fff]", body)
        if len(substantive) < 4:
            return True
        if len(substantive) / max(len(body), 1) < 0.45:
            return True
        return False

    async def _compact_auto_memories(self, *, group_limit: int = 24) -> dict:
        """One-click cleanup + compression for auto-extracted memories."""
        mem = self._gateway.memory
        rows = mem.conn.execute(
            "SELECT n.note_id, n.title, n.tags, n.created, b.body "
            "FROM notes n LEFT JOIN note_bodies b USING(note_id) "
            "ORDER BY n.created DESC"
        ).fetchall()
        notes = []
        for r in rows:
            tags_raw = r["tags"] or "[]"
            try:
                tags = json.loads(tags_raw)
            except Exception:
                tags = []
            notes.append({
                "note_id": r["note_id"],
                "title": r["title"] or "",
                "tags": tags,
                "created": int(r["created"] or 0),
                "body": r["body"] or "",
            })

        auto_notes = [n for n in notes if self._is_auto_extract_note(n)]
        junk = [n for n in auto_notes if (not self._is_compacted_auto_note(n)) and self._is_low_value_auto_note(n)]

        deleted_junk = 0
        for n in junk:
            if mem.delete_note(n["note_id"]):
                deleted_junk += 1

        # Rebuild candidate list after junk deletion; keep compact notes as-is.
        keep_auto = [
            n for n in auto_notes
            if n["note_id"] not in {x["note_id"] for x in junk} and not self._is_compacted_auto_note(n)
        ]
        by_day: dict[str, list[dict]] = {}
        for n in keep_auto:
            day = time.strftime("%Y-%m-%d", time.localtime(n["created"] or int(time.time())))
            by_day.setdefault(day, []).append(n)

        compressed_groups = 0
        compressed_sources = 0
        for day, items in by_day.items():
            if len(items) < 3:
                continue
            uniq: list[str] = []
            seen_lines: set[str] = set()
            for it in sorted(items, key=lambda x: x["created"]):
                line = self._clean_auto_body(it.get("body", ""))
                if not line or line in seen_lines:
                    continue
                seen_lines.add(line)
                uniq.append(line)
                if len(uniq) >= group_limit:
                    break
            if len(uniq) < 3:
                continue
            title = f"Auto Summary {day}"
            content = "\n".join(f"- {x}" for x in uniq)

            # LLM-based semantic distillation: compress bullet list into a
            # deduplicated, structured paragraph/bullets using the AI.
            # Falls back to the regex-deduped bullet list if LLM is unavailable.
            try:
                from hushclaw.providers.base import Message as _Msg
                provider = self._gateway.base_agent.provider
                model = self._gateway.base_agent.config.agent.model
                distill_prompt = (
                    f"以下是 {day} 从对话中自动提取的零散记忆条目，请语义去重并提炼为简洁摘要。\n"
                    "要求：合并相似内容，删除重复或低价值条目，用 2-6 个 bullet 列出核心事实。\n"
                    "直接输出 bullet 列表，不要前言和解释。\n\n"
                    + content
                )
                resp = await provider.complete(
                    messages=[_Msg(role="user", content=distill_prompt)],
                    system="You are a memory curator. Output only a concise bullet list of unique facts.",
                    max_tokens=400,
                    model=model,
                )
                if resp.content and resp.content.strip():
                    content = resp.content.strip()
            except Exception as _e:
                from hushclaw.util.logging import get_logger as _get_logger
                _get_logger("server").warning(
                    "compact_auto_memories: LLM distillation failed, using regex result: %s", _e
                )

            mem.remember(
                content,
                title=title,
                tags=["_auto_extract", "_auto_compact"],
            )
            compressed_groups += 1
            for it in items:
                if mem.delete_note(it["note_id"]):
                    compressed_sources += 1

        return {
            "deleted_junk": deleted_junk,
            "compressed_groups": compressed_groups,
            "compressed_sources": compressed_sources,
            "auto_total_before": len(auto_notes),
        }
