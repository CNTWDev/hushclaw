"""Tests for server slash-command handling with prompt-only skills."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hushclaw.memory.store import MemoryStore
from hushclaw.server import HushClawServer


class _MockWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class _MockSkillRegistry:
    def __init__(self, skill: dict | None):
        self._skill = skill

    def get(self, name: str):
        if self._skill and self._skill.get("name") == name:
            return self._skill
        return None

    def list_all(self):
        return [self._skill] if self._skill else []


class TestServerSlashPromptOnlySkills(unittest.IsolatedAsyncioTestCase):
    def _make_server(self, skill_meta: dict) -> HushClawServer:
        server = HushClawServer.__new__(HushClawServer)
        reg = _MockSkillRegistry(skill_meta)
        pool = SimpleNamespace(_agent=SimpleNamespace(_skill_registry=reg))
        server._gateway = SimpleNamespace(
            get_pool=lambda _name: pool,
            base_agent=SimpleNamespace(_skill_registry=reg),
        )
        server._pending_skill_prompts = {}
        return server

    async def test_prompt_only_skill_without_args_asks_for_short_requirement(self):
        server = self._make_server(
            {"name": "ai-news-summary", "description": "Summarize AI news", "available": True, "direct_tool": ""}
        )
        ws = _MockWs()

        handled, ok, next_text = await server._try_handle_slash_command(
            ws, "default", "s-1", "/ai-news-summary"
        )

        self.assertTrue(handled)
        self.assertTrue(ok)
        self.assertEqual(next_text, "/ai-news-summary")
        self.assertEqual(server._pending_skill_prompts.get("s-1", {}).get("skill"), "ai-news-summary")
        self.assertTrue(ws.sent)
        self.assertEqual(ws.sent[-1].get("type"), "done")
        self.assertIn("Please add one short requirement", ws.sent[-1].get("text", ""))

    async def test_prompt_only_skill_with_args_rewrites_to_normal_chat(self):
        server = self._make_server(
            {"name": "ai-news-summary", "description": "Summarize AI news", "available": True, "direct_tool": ""}
        )
        ws = _MockWs()

        handled, ok, next_text = await server._try_handle_slash_command(
            ws, "default", "s-2", "/ai-news-summary today open-source updates"
        )

        self.assertFalse(handled)
        self.assertTrue(ok)
        self.assertIn("[SkillCommand /ai-news-summary]", next_text)
        self.assertIn("today open-source updates", next_text)
        self.assertFalse(ws.sent)


class TestServerMemoryHelpers(unittest.TestCase):
    def test_is_auto_extract_note(self):
        self.assertTrue(HushClawServer._is_auto_extract_note({"tags": ["_auto_extract", "x"]}))
        self.assertFalse(HushClawServer._is_auto_extract_note({"tags": ["manual"]}))
        self.assertFalse(HushClawServer._is_auto_extract_note({"tags": []}))

    def test_normalize_note_payload_prefers_created(self):
        out = HushClawServer._normalize_note_payload({"created": 123, "modified": 456})
        self.assertEqual(out["created_at"], 123)
        self.assertEqual(out["updated_at"], 456)

    def test_normalize_note_payload_falls_back_to_modified(self):
        out = HushClawServer._normalize_note_payload({"modified": 456})
        self.assertEqual(out["created_at"], 456)

    def test_compact_auto_memories_deletes_junk_and_merges_valid(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            junk_id = mem.remember("并保存到记忆中", title="Auto: 并保存到记忆中", tags=["_auto_extract"])
            a_id = mem.remember("尼日利亚市场周报要点A", title="Auto: 要点A", tags=["_auto_extract"])
            b_id = mem.remember("尼日利亚市场周报要点B", title="Auto: 要点B", tags=["_auto_extract"])
            c_id = mem.remember("尼日利亚市场周报要点C", title="Auto: 要点C", tags=["_auto_extract"])
            manual_id = mem.remember("用户手工记忆", title="Manual: note", tags=["manual"])

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)

            stats = server._compact_auto_memories(group_limit=10)
            self.assertGreaterEqual(stats["deleted_junk"], 1)
            self.assertGreaterEqual(stats["compressed_groups"], 1)
            self.assertGreaterEqual(stats["compressed_sources"], 3)

            notes = mem.list_recent_notes(limit=100)
            ids = {n.get("note_id") for n in notes}
            self.assertIn(manual_id, ids)
            self.assertNotIn(junk_id, ids)
            self.assertNotIn(a_id, ids)
            self.assertNotIn(b_id, ids)
            self.assertNotIn(c_id, ids)

            compacted = [n for n in notes if "_auto_compact" in (n.get("tags") or [])]
            self.assertTrue(compacted)
            mem.close()


class TestServerSkillsList(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_includes_memory_skills(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.remember("tiktok insight workflow", title="tiktok-insight", tags=["_skill"])

            reg = _MockSkillRegistry(None)
            reg._skills = {}
            base_cfg = SimpleNamespace(
                tools=SimpleNamespace(skill_dir=None, user_skill_dir=None),
                agent=SimpleNamespace(workspace_dir=None),
            )
            base_agent = SimpleNamespace(_skill_registry=reg, config=base_cfg)
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=base_agent, memory=mem)
            ws = _MockWs()

            await server._handle_list_skills(ws)
            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "skills")
            names = [i.get("name") for i in msg.get("items", [])]
            self.assertIn("tiktok-insight", names)
            item = next(i for i in msg["items"] if i.get("name") == "tiktok-insight")
            self.assertEqual(item.get("scope"), "memory")
            mem.close()
