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


class TestServerMemoryHelpers(unittest.IsolatedAsyncioTestCase):
    def test_is_auto_extract_note(self):
        self.assertTrue(HushClawServer._is_auto_extract_note({"tags": ["_auto_extract", "x"]}))
        self.assertFalse(HushClawServer._is_auto_extract_note({"tags": ["manual"]}))
        self.assertFalse(HushClawServer._is_auto_extract_note({"tags": []}))

    def test_is_system_note_hides_skill_usage_telemetry(self):
        self.assertTrue(HushClawServer._is_system_note({"tags": ["_skill_usage", "deep-research"]}))
        self.assertFalse(HushClawServer._is_system_note({"tags": ["manual"]}))

    def test_is_system_note_hides_telemetry_and_session_memory(self):
        self.assertTrue(HushClawServer._is_system_note({"tags": [], "memory_kind": "telemetry"}))
        self.assertTrue(HushClawServer._is_system_note({"tags": [], "memory_kind": "session_memory"}))

    def test_normalize_memory_kind_filter_accepts_all(self):
        out = HushClawServer._normalize_memory_kind_filter(["all"])
        self.assertIn("user_model", out)
        self.assertIn("project_knowledge", out)
        self.assertIn("decision", out)
        self.assertIn("session_memory", out)
        self.assertIn("telemetry", out)

    def test_normalize_note_payload_prefers_created(self):
        out = HushClawServer._normalize_note_payload({"created": 123, "modified": 456})
        self.assertEqual(out["created_at"], 123)
        self.assertEqual(out["updated_at"], 456)

    def test_normalize_note_payload_falls_back_to_modified(self):
        out = HushClawServer._normalize_note_payload({"modified": 456})
        self.assertEqual(out["created_at"], 456)

    async def test_get_memory_overview_returns_product_summary(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.remember(
                "用户偏好直接、务实的工程沟通",
                title="Communication preference",
                tags=["manual"],
                note_type="preference",
                memory_kind="user_model",
            )
            mem.remember(
                "工程质量需要用测试和边界条件证明。",
                title="Engineering belief",
                tags=["domain:engineering"],
                note_type="belief",
                memory_kind="user_model",
            )
            mem.user_profile.upsert_fact(
                category="communication_style",
                key="direct",
                value={"summary": "prefers direct, pragmatic answers"},
                confidence=0.9,
                source_session_id="s-1",
            )
            mem.record_reflection(
                session_id="s-1",
                task_fingerprint="code_fix",
                success=True,
                outcome="fixed",
                lesson="Check existing module boundaries before editing.",
                strategy_hint="Read related files first.",
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._handle_get_memory_overview(ws, {})

            self.assertTrue(ws.sent)
            payload = ws.sent[-1]
            self.assertEqual(payload["type"], "memory_overview")
            self.assertEqual(payload["profile"]["total"], 1)
            self.assertEqual(payload["beliefs"]["total"], 1)
            self.assertEqual(payload["reflections"]["total_recent"], 1)
            self.assertEqual(payload["taxonomy"]["context"]["time_horizon"], "now")
            self.assertIn("conceptual_priority", payload["taxonomy"])
            self.assertEqual(
                payload["taxonomy"]["injection_order"][:3],
                ["date", "user_notes", "profile"],
            )
            self.assertEqual(
                payload["profile"]["high_confidence_facts"][0]["time_horizon"],
                "long_term",
            )
            self.assertEqual(payload["beliefs"]["top_domains"][0]["time_horizon"], "mid_term")
            self.assertEqual(payload["reflections"]["latest_lessons"][0]["time_horizon"], "learning")
            self.assertTrue(payload["memories"]["recent_items"][0]["time_horizon"])
            self.assertIn("effective_weight", payload["memories"]["recent_items"][0])
            self.assertEqual(
                payload["profile"]["high_confidence_facts"][0]["source"]["session_id"],
                "s-1",
            )
            self.assertEqual(
                payload["reflections"]["latest_lessons"][0]["source"]["session_id"],
                "s-1",
            )
            self.assertTrue(payload["beliefs"]["top_domains"][0]["entries"][0]["source"]["note_id"])
            self.assertTrue(payload["memories"]["recent_items"])
            mem.close()

    async def test_compact_auto_memories_deletes_junk_and_merges_valid(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            junk_id = mem.remember("并保存到记忆中", title="Auto: 并保存到记忆中", tags=["_auto_extract"])
            a_id = mem.remember("尼日利亚市场周报要点A", title="Auto: 要点A", tags=["_auto_extract"])
            b_id = mem.remember("尼日利亚市场周报要点B", title="Auto: 要点B", tags=["_auto_extract"])
            c_id = mem.remember("尼日利亚市场周报要点C", title="Auto: 要点C", tags=["_auto_extract"])
            manual_id = mem.remember("用户手工记忆", title="Manual: note", tags=["manual"])

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)

            stats = await server._compact_auto_memories(group_limit=10)
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


class TestServerAttachmentProcessing(unittest.TestCase):
    def test_process_attachments_keeps_image_context_in_text(self):
        import tempfile

        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
        )

        with tempfile.TemporaryDirectory() as d:
            image_path = Path(d) / "sample.png"
            image_path.write_bytes(png_bytes)

            server = HushClawServer.__new__(HushClawServer)
            server._upload_dir = Path(d)
            server._lookup_uploaded_file = lambda _fid: {"storage_path": str(image_path)}

            text, images = server._process_attachments(
                "Please inspect this image",
                [{"file_id": "img-1", "name": "sample.png", "url": "/files/img-1"}],
            )

            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].startswith("data:image/png;base64,"))
            self.assertIn("[Attached files]", text)
            self.assertIn("sample.png", text)
            self.assertIn("image", text)
            self.assertIn(str(image_path), text)


class TestServerSkillsList(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_returns_registry_items_only(self):
        """Skills come from SKILL.md files only — memory is not consulted."""
        import tempfile, json
        from pathlib import Path
        from hushclaw.skills.writer import write_skill

        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d)
            write_skill("tiktok-insight", "Playbook for TikTok", "TikTok workflow", skill_dir)

            from hushclaw.skills.loader import SkillRegistry
            reg = SkillRegistry(skill_dir)

            base_cfg = SimpleNamespace(
                tools=SimpleNamespace(skill_dir=skill_dir, user_skill_dir=None),
                agent=SimpleNamespace(workspace_dir=None),
            )
            base_agent = SimpleNamespace(_skill_registry=reg, config=base_cfg)
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=base_agent)
            ws = _MockWs()

            await server._handle_list_skills(ws)
            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "skills")
            names = [i.get("name") for i in msg.get("items", [])]
            self.assertIn("tiktok-insight", names)
            item = next(i for i in msg["items"] if i.get("name") == "tiktok-insight")
            self.assertEqual(item.get("scope"), "system")
            # Memory is never consulted
            self.assertNotIn("memory", [i.get("scope") for i in msg["items"]])


class TestServerSessionApis(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_search_sessions_returns_matches(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.save_turn("session-a", "user", "Investigate payment retry strategy")
            mem.save_turn("session-b", "user", "Prepare travel checklist")

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "search_sessions", "query": "payment retry"})

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "session_search_results")
            ids = [i.get("session_id") for i in msg.get("items", [])]
            self.assertIn("session-a", ids)
            mem.close()

    async def test_dispatch_get_session_history_includes_summary_and_lineage(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            sid = "session-a"
            mem.save_turn(sid, "user", "Investigate payment retry strategy")
            mem.save_session_summary(sid, "Retry strategy summary")
            mem.record_session_compaction(sid, archived=3, kept=1)

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "get_session_history", "session_id": sid})

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "session_history")
            self.assertEqual(msg.get("summary"), "Retry strategy summary")
            self.assertTrue(msg.get("lineage"))
            self.assertEqual(msg["lineage"][0]["relationship"], "compacted")
            mem.close()

    async def test_dispatch_get_learning_state_returns_profile_reflections_and_skill_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.user_profile.upsert_fact(
                category="communication_style",
                key="response_depth",
                value={"value": "concise", "summary": "User prefers concise answers."},
                confidence=0.9,
                source_session_id="sess-1",
            )
            mem.record_reflection(
                session_id="sess-1",
                task_fingerprint="web_research",
                success=True,
                outcome="Delivered summary",
                lesson="Preserve the workflow",
                strategy_hint="fetch_url -> summarize",
                skill_name="deep-research",
                source_turn_count=1,
            )
            mem.record_skill_outcome(
                skill_name="deep-research",
                session_id="sess-1",
                task_fingerprint="web_research",
                success=True,
                note="Worked",
            )
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "get_learning_state"})

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "learning_state")
            self.assertTrue(msg.get("profile_snapshot"))
            self.assertTrue(msg.get("reflections"))
            self.assertTrue(msg.get("skill_outcomes"))
            mem.close()

    async def test_dispatch_delete_profile_fact_removes_profile_fact_only(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            fact_id = mem.user_profile.upsert_fact(
                category="preferences",
                key="tone",
                value={"summary": "prefers direct answers"},
                confidence=0.8,
            )
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "delete_profile_fact", "fact_id": fact_id})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "profile_fact_deleted")
            self.assertTrue(msg.get("ok"))
            self.assertFalse(mem.user_profile.list_facts(limit=10))
            mem.close()


class TestServerMemoryApis(unittest.IsolatedAsyncioTestCase):
    async def test_list_memories_excludes_legacy_skill_usage_notes(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.remember(
                "skill_used: deep-research session=abcd1234",
                title="Skill call: deep-research",
                tags=["_skill_usage", "deep-research"],
                persist_to_disk=False,
            )
            mem.remember("用户偏好更喜欢简洁回答", title="User preference", tags=["manual"])

            server = HushClawServer.__new__(HushClawServer)
            base_agent = SimpleNamespace(
                memory=mem,
                list_memories=lambda limit=20, offset=0, tag=None, exclude_tags=None, include_kinds=None: (
                    mem.search_by_tag(tag, limit=limit)
                    if tag else mem.list_recent_notes(
                        limit=limit,
                        offset=offset,
                        exclude_tags=exclude_tags,
                        include_kinds=include_kinds,
                    )
                ),
                search=lambda query, limit=5, include_kinds=None: mem.search(query, limit=limit, include_kinds=include_kinds),
            )
            server._gateway = SimpleNamespace(base_agent=base_agent)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_memories", "limit": 20})

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "memories")
            titles = [i.get("title") for i in msg.get("items", [])]
            self.assertIn("User preference", titles)
            self.assertNotIn("Skill call: deep-research", titles)
            mem.close()

    async def test_list_memories_excludes_telemetry_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.remember("The user prefers concise answers", title="User preference", note_type="preference")
            mem.remember("Correction in session", title="Correction", memory_kind="telemetry", persist_to_disk=False)

            server = HushClawServer.__new__(HushClawServer)
            base_agent = SimpleNamespace(
                memory=mem,
                list_memories=lambda limit=20, offset=0, tag=None, exclude_tags=None, include_kinds=None: (
                    mem.search_by_tag(tag, limit=limit)
                    if tag else mem.list_recent_notes(
                        limit=limit,
                        offset=offset,
                        exclude_tags=exclude_tags,
                        include_kinds=include_kinds,
                    )
                ),
                search=lambda query, limit=5, include_kinds=None: mem.search(query, limit=limit, include_kinds=include_kinds),
            )
            server._gateway = SimpleNamespace(base_agent=base_agent)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_memories", "limit": 20})

            msg = ws.sent[-1]
            titles = [i.get("title") for i in msg.get("items", [])]
            self.assertIn("User preference", titles)
            self.assertNotIn("Correction", titles)
            mem.close()
