"""Tests for server slash-command handling with prompt-only skills."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class TestServerLogsPanel(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_get_logs_returns_recent_filtered_logs(self):
        from hushclaw.util.logging import recent_logs, setup_logging

        setup_logging("INFO")
        logging.getLogger("hushclaw.test.logs").warning("logs panel sentinel")
        self.assertTrue(any("logs panel sentinel" in item["message"] for item in recent_logs(query="sentinel")))

        server = HushClawServer.__new__(HushClawServer)
        ws = _MockWs()

        await server._dispatch(ws, {"type": "get_logs", "query": "sentinel", "level": "WARNING", "limit": 20})

        self.assertTrue(ws.sent)
        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "logs")
        self.assertTrue(msg.get("ok"))
        self.assertTrue(any("logs panel sentinel" in item.get("message", "") for item in msg.get("items", [])))

    async def test_dispatch_get_logs_handles_invalid_limit(self):
        server = HushClawServer.__new__(HushClawServer)
        ws = _MockWs()

        await server._dispatch(ws, {"type": "get_logs", "limit": "not-a-number"})

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "logs")
        self.assertTrue(msg.get("ok"))
        self.assertIsInstance(msg.get("items"), list)

    async def test_dispatch_get_logs_returns_structured_error(self):
        server = HushClawServer.__new__(HushClawServer)
        ws = _MockWs()

        with patch("hushclaw.util.logging.recent_logs", side_effect=RuntimeError("boom")):
            await server._dispatch(ws, {"type": "get_logs", "limit": 20})

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "logs")
        self.assertFalse(msg.get("ok"))
        self.assertEqual(msg.get("items"), [])
        self.assertIn("boom", msg.get("error", ""))


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


class TestAgentWorkbenchTest(unittest.IsolatedAsyncioTestCase):
    async def test_test_agent_returns_inline_result(self):
        server = HushClawServer.__new__(HushClawServer)

        async def _event_stream(agent, text, session_id=None, **kwargs):
            yield {"type": "done", "text": f"{agent}:{text}:{session_id}"}

        server._gateway = SimpleNamespace(
            get_agent_def=lambda name: {"name": name} if name == "writer" else None,
            event_stream=_event_stream,
        )
        ws = _MockWs()

        await server._handle_test_agent(
            ws,
            {
                "agent": "writer",
                "text": "hello",
                "request_id": "r-1",
                "session_id": "agent-test-writer",
            },
        )

        self.assertEqual(ws.sent[-1]["type"], "agent_test_result")
        self.assertTrue(ws.sent[-1]["ok"])
        self.assertEqual(ws.sent[-1]["agent"], "writer")
        self.assertEqual(ws.sent[-1]["request_id"], "r-1")
        self.assertIn("writer:hello:agent-test-writer", ws.sent[-1]["text"])

    async def test_test_agent_rejects_unknown_agent(self):
        server = HushClawServer.__new__(HushClawServer)
        server._gateway = SimpleNamespace(get_agent_def=lambda _name: None)
        ws = _MockWs()

        await server._handle_test_agent(ws, {"agent": "missing", "text": "hello"})

        self.assertEqual(ws.sent[-1]["type"], "agent_test_result")
        self.assertFalse(ws.sent[-1]["ok"])
        self.assertIn("Unknown agent", ws.sent[-1]["error"])


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
            mem.save_belief_model_consolidation(
                domain="engineering",
                scope="global",
                current_stance="Engineering quality must be proven with tests and boundary cases.",
                summary="User evaluates engineering quality through evidence.",
                trajectory="Stable evidence-first engineering stance.",
                change_drivers=["boundary case failures"],
                signals=["tests", "edge cases"],
            )
            mem.save_belief_model_consolidation(
                domain="engineering",
                scope="global",
                current_stance="Engineering quality must be proven with tests and boundary cases.",
                summary="User evaluates engineering quality through evidence.",
                trajectory="Stable evidence-first engineering stance.",
                change_drivers=["boundary case failures"],
                signals=["tests", "edge cases"],
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


class TestServerFilesList(unittest.IsolatedAsyncioTestCase):
    async def test_list_files_sorts_by_created_but_shows_persisted_modified_time(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(data_dir=Path(d) / "memory")
            upload_dir = Path(d) / "uploads"
            upload_dir.mkdir()
            new_path = upload_dir / "new.md"
            old_path = upload_dir / "old.md"
            new_path.write_text("new", encoding="utf-8")
            old_path.write_text("old", encoding="utf-8")
            os.utime(new_path, (3000, 3000))
            os.utime(old_path, (9000, 9000))

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=SimpleNamespace(memory=mem))
            server._upload_dir = upload_dir
            server._upload_index_backfilled = True
            server._file_url = lambda file_id: f"/files/{file_id}"

            conn = mem.conn
            conn.execute(
                "INSERT INTO file_blobs(blob_id, sha256, storage_path, size_bytes, mime_type, created) "
                "VALUES ('blob-new', 'sha-new', ?, 20, 'text/markdown', 1111)",
                (str(new_path),),
            )
            conn.execute(
                "INSERT INTO file_blobs(blob_id, sha256, storage_path, size_bytes, mime_type, created) "
                "VALUES ('blob-old', 'sha-old', ?, 10, 'text/markdown', 2222)",
                (str(old_path),),
            )
            conn.execute(
                "INSERT INTO uploaded_files(file_id, blob_id, original_name, display_name, source, created, modified, last_used, deleted) "
                "VALUES ('file-new', 'blob-new', 'new.md', 'new.md', 'generated', 2000, 3000, 3000, 0)"
            )
            conn.execute(
                "INSERT INTO uploaded_files(file_id, blob_id, original_name, display_name, source, created, modified, last_used, deleted) "
                "VALUES ('file-old', 'blob-old', 'old.md', 'old.md', 'generated', 1000, 9000, 9000, 0)"
            )
            conn.commit()

            ws = _MockWs()
            await server._handle_list_files(ws, {"limit": 10})

            items = ws.sent[0]["items"]
            self.assertEqual([item["file_id"] for item in items], ["file-new", "file-old"])
            self.assertEqual(items[0]["created"], 2000)
            self.assertEqual(items[0]["modified"], 3000)
            self.assertEqual(items[1]["created"], 1000)
            self.assertEqual(items[1]["modified"], 9000)
            mem.close()

    async def test_list_files_filters_by_query_source_and_paginates_matches(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(data_dir=Path(d) / "memory")
            upload_dir = Path(d) / "uploads"
            upload_dir.mkdir()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=SimpleNamespace(memory=mem))
            server._upload_dir = upload_dir
            server._upload_index_backfilled = True
            server._file_url = lambda file_id: f"/files/{file_id}"

            conn = mem.conn
            fixtures = [
                ("file-alpha-new", "blob-alpha-new", "alpha-report.md", "Alpha Report", "generated", 3000),
                ("file-alpha-old", "blob-alpha-old", "notes.md", "alpha notes", "generated", 2000),
                ("file-beta", "blob-beta", "beta-report.md", "Beta Report", "generated", 1000),
                ("file-alpha-upload", "blob-alpha-upload", "alpha-upload.md", "Alpha Upload", "upload", 4000),
            ]
            for file_id, blob_id, original_name, display_name, source, created in fixtures:
                path = upload_dir / f"{blob_id}.md"
                path.write_text(file_id, encoding="utf-8")
                conn.execute(
                    "INSERT INTO file_blobs(blob_id, sha256, storage_path, size_bytes, mime_type, created) "
                    "VALUES (?, ?, ?, 10, 'text/markdown', ?)",
                    (blob_id, f"sha-{blob_id}", str(path), created),
                )
                conn.execute(
                    "INSERT INTO uploaded_files(file_id, blob_id, original_name, display_name, source, artifact_url, created, modified, last_used, deleted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (file_id, blob_id, original_name, display_name, source, f"/files/{file_id}", created, created, created),
                )
            conn.commit()

            ws = _MockWs()
            await server._handle_list_files(
                ws,
                {"limit": 1, "offset": 1, "source": "generated", "query": "alpha"},
            )

            payload = ws.sent[0]
            self.assertEqual(payload["total"], 2)
            self.assertTrue(payload["has_more"] is False)
            self.assertEqual(payload["offset"], 1)
            self.assertEqual([item["file_id"] for item in payload["items"]], ["file-alpha-old"])
            mem.close()

    async def test_list_files_returns_next_cursor_and_follows_cursor_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(data_dir=Path(d) / "memory")
            upload_dir = Path(d) / "uploads"
            upload_dir.mkdir()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=SimpleNamespace(memory=mem))
            server._upload_dir = upload_dir
            server._upload_index_backfilled = True
            server._file_url = lambda file_id: f"/files/{file_id}"

            conn = mem.conn
            fixtures = [
                ("file-page-0", "blob-page-0", 3000),
                ("file-page-1", "blob-page-1", 2000),
                ("file-page-2", "blob-page-2", 1000),
            ]
            for file_id, blob_id, created in fixtures:
                path = upload_dir / f"{blob_id}.md"
                path.write_text(file_id, encoding="utf-8")
                conn.execute(
                    "INSERT INTO file_blobs(blob_id, sha256, storage_path, size_bytes, mime_type, created) "
                    "VALUES (?, ?, ?, 10, 'text/markdown', ?)",
                    (blob_id, f"sha-{blob_id}", str(path), created),
                )
                conn.execute(
                    "INSERT INTO uploaded_files(file_id, blob_id, original_name, display_name, source, artifact_url, created, modified, last_used, deleted) "
                    "VALUES (?, ?, ?, ?, 'generated', ?, ?, ?, ?, 0)",
                    (file_id, blob_id, f"{file_id}.md", file_id, f"/files/{file_id}", created, created, created),
                )
            conn.commit()

            ws = _MockWs()
            await server._handle_list_files(ws, {"limit": 2})

            first = ws.sent[0]
            self.assertEqual(first["type"], "files")
            self.assertTrue(first["has_more"])
            self.assertTrue(first["next_cursor"])
            self.assertEqual([item["file_id"] for item in first["items"]], ["file-page-0", "file-page-1"])

            ws2 = _MockWs()
            await server._handle_list_files(ws2, {"limit": 2, "cursor": first["next_cursor"]})
            second = ws2.sent[0]
            self.assertEqual(second["cursor"], first["next_cursor"])
            self.assertFalse(second["has_more"])
            self.assertEqual([item["file_id"] for item in second["items"]], ["file-page-2"])
            mem.close()

    async def test_list_files_upload_filter_includes_legacy_ws_upload_source(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(data_dir=Path(d) / "memory")
            upload_dir = Path(d) / "uploads"
            upload_dir.mkdir()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(base_agent=SimpleNamespace(memory=mem))
            server._upload_dir = upload_dir
            server._upload_index_backfilled = True
            server._file_url = lambda file_id: f"/files/{file_id}"

            conn = mem.conn
            fixtures = [
                ("file-upload", "blob-upload", "upload", 3000),
                ("file-ws-upload", "blob-ws-upload", "ws_upload", 2000),
                ("file-generated", "blob-generated", "generated", 1000),
            ]
            for file_id, blob_id, source, created in fixtures:
                path = upload_dir / f"{blob_id}.md"
                path.write_text(file_id, encoding="utf-8")
                conn.execute(
                    "INSERT INTO file_blobs(blob_id, sha256, storage_path, size_bytes, mime_type, created) "
                    "VALUES (?, ?, ?, 10, 'text/markdown', ?)",
                    (blob_id, f"sha-{blob_id}", str(path), created),
                )
                conn.execute(
                    "INSERT INTO uploaded_files(file_id, blob_id, original_name, display_name, source, artifact_url, created, modified, last_used, deleted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (file_id, blob_id, f"{file_id}.md", file_id, source, f"/files/{file_id}", created, created, created),
                )
            conn.commit()

            ws = _MockWs()
            await server._handle_list_files(ws, {"limit": 10, "source": "upload"})

            payload = ws.sent[0]
            self.assertEqual([item["file_id"] for item in payload["items"]], ["file-upload", "file-ws-upload"])
            mem.close()


class TestServerSkillsList(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_returns_registry_items_only(self):
        """Skills come from SKILL.md files only — memory is not consulted."""
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
    async def test_dispatch_create_agent_returns_detail_and_list(self):
        class _Gateway:
            def __init__(self):
                self.created = []

            def create_agent(self, **kwargs):
                self.created.append(kwargs)

            def get_agent_def(self, name):
                return {
                    "name": name,
                    "description": "Executive AI org design",
                    "routing_tags": ["exec", "org"],
                }

            def list_agents(self):
                return [
                    {
                        "name": "default",
                        "description": "Default agent",
                        "routing_tags": ["general"],
                    },
                    self.get_agent_def("exec-ai-org-design"),
                ]

        server = HushClawServer.__new__(HushClawServer)
        server._gateway = _Gateway()
        ws = _MockWs()

        await server._dispatch(ws, {
            "type": "create_agent",
            "name": "exec-ai-org-design",
            "description": "Executive AI org design",
            "routing_tags": ["exec", "org"],
        })

        msg = ws.sent[-1]
        self.assertEqual(msg["type"], "agent_created")
        self.assertTrue(msg["ok"])
        self.assertEqual(msg["agent"]["name"], "exec-ai-org-design")
        self.assertIn("exec-ai-org-design", [item["name"] for item in msg["agents"]])

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

    async def test_dispatch_list_work_tasks_honors_status_filter(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            done_task = mem.create_task("Completed task")
            queued_task = mem.create_task("Queued task")
            run = mem.claim_task(done_task["task_id"], worker_id="tester")
            self.assertIsNotNone(run)
            self.assertTrue(mem.complete_task_run(run["run_id"], "done"))

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_work_tasks", "status": "done"})

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "work_tasks")
            ids = [i.get("task_id") for i in msg.get("tasks", [])]
            self.assertEqual(ids, [done_task["task_id"]])
            self.assertNotIn(queued_task["task_id"], ids)
            mem.close()

    async def test_run_work_task_notify_broadcasts_events_not_lists(self):
        class _Scheduler:
            async def run_work_task_now(self, task_id, *, agent, worker_id, on_started):
                await on_started({"task_id": task_id, "run": {"run_id": "run-1"}, "session_id": "sess-1"})
                return {"ok": True, "task_id": task_id, "run_id": "run-1", "result": "done"}

        server = HushClawServer.__new__(HushClawServer)
        server._scheduler = _Scheduler()
        ws = _MockWs()
        server._connected_clients = {ws}

        await server._run_work_task_and_notify("task-1", agent="default")

        types = [msg.get("type") for msg in ws.sent]
        self.assertEqual(types, ["work_task_started", "work_task_run_result"])
        self.assertNotIn("work_tasks", types)

    async def test_list_sessions_includes_runtime_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            sid = "session-a"
            mem.save_turn(sid, "user", "Investigate payment retry strategy")

            class _GatewayCfg:
                session_list_limit = 20
                session_list_hide_scheduled = False
                session_list_idle_days = 365

            class _Config:
                gateway = _GatewayCfg()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(
                memory=mem,
                base_agent=SimpleNamespace(config=_Config()),
            )
            server._session_tasks = {}
            server._session_runtime = {
                sid: {
                    "session_id": sid,
                    "status": "running",
                    "phase": "tool_call",
                    "summary": "Using browser_wait_for_user",
                    "agent": "default",
                    "started_at": 1,
                    "updated_at": 2,
                    "last_error": "",
                    "requires_user": False,
                }
            }
            server._os_api = None
            ws = _MockWs()

            await server._handle_list_sessions(ws, {"limit": 10})

            msg = ws.sent[-1]
            self.assertEqual(msg["type"], "sessions")
            item = next(i for i in msg["items"] if i["session_id"] == sid)
            self.assertEqual(item["runtime"]["status"], "running")
            self.assertEqual(item["runtime"]["phase"], "tool_call")
            mem.close()

    async def test_list_sessions_returns_next_cursor_for_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            now = int(time.time())
            for idx in range(3):
                sid = f"session-page-{idx}"
                mem.save_turn(sid, "user", f"Message {idx}")
                mem.conn.execute("UPDATE sessions SET last_turn=? WHERE session_id=?", (now - idx, sid))
            mem.conn.commit()

            class _GatewayCfg:
                session_list_limit = 20
                session_list_hide_scheduled = False
                session_list_idle_days = 365

            class _Config:
                gateway = _GatewayCfg()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(
                memory=mem,
                base_agent=SimpleNamespace(config=_Config()),
            )
            server._session_tasks = {}
            server._session_runtime = {}
            server._os_api = None
            ws = _MockWs()

            await server._handle_list_sessions(ws, {"limit": 2})

            msg = ws.sent[-1]
            self.assertEqual(msg["type"], "sessions")
            self.assertTrue(msg["has_more"])
            self.assertTrue(msg["next_cursor"])
            self.assertFalse(msg["append"])
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

    async def test_dispatch_get_session_history_preserves_event_millisecond_ts(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            sid = "session-ms"
            mem.session_log.append(
                sid,
                "user_message_received",
                {"input": "Check timestamp rendering"},
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "get_session_history", "session_id": sid})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "session_history")
            self.assertEqual(msg["turns"][0]["role"], "user")
            self.assertGreater(msg["turns"][0]["ts"], 1_000_000_000_000)
            mem.close()

    async def test_dispatch_get_session_history_includes_runtime_recent_events(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            sid = "session-runtime"
            mem.save_turn(sid, "user", "Check runtime restore")
            ts = int(time.time() * 1000)
            mem.conn.execute(
                "INSERT INTO events(session_id, type, payload_json, ts) VALUES (?, ?, ?, ?)",
                (sid, "ws:tool_call", json.dumps({"tool": "browser"}), ts),
            )
            mem.conn.execute(
                "INSERT INTO events(session_id, type, payload_json, ts) VALUES (?, ?, ?, ?)",
                (sid, "ws:child_run_state_changed", json.dumps({
                    "run_id": "run-child",
                    "parent_run_id": "run-parent",
                    "run_kind": "child",
                    "agent": "researcher",
                    "state": "running",
                    "summary": "Checking sources",
                }), ts + 1),
            )
            mem.conn.commit()

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            server._session_tasks = {}
            server._session_runtime = {
                sid: {
                    "session_id": sid,
                    "status": "running",
                    "summary": "Working",
                    "updated_at": ts + 2,
                }
            }
            ws = _MockWs()

            await server._dispatch(ws, {"type": "get_session_history", "session_id": sid})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "session_history")
            runtime = msg.get("runtime") or {}
            self.assertEqual(runtime.get("status"), "running")
            self.assertEqual(len(runtime.get("recent_events") or []), 2)
            self.assertEqual(runtime["recent_events"][0]["label"], "browser")
            self.assertEqual(runtime["recent_events"][1]["scope"], "child")
            mem.close()

    async def test_subscribe_session_does_not_emit_partial_replay_chunk(self):
        server = HushClawServer.__new__(HushClawServer)
        sid = "session-running"
        entry = SimpleNamespace(
            text="partial answer",
            buffer=[],
            memory=None,
            subscriber=None,
            is_running=lambda: True,
        )
        server._session_tasks = {sid: entry}
        ws = _MockWs()

        await server._subscribe_session(ws, sid)

        self.assertFalse(any(msg.get("type") == "chunk" for msg in ws.sent))

    async def test_subscribe_session_skips_partial_replay_chunk_after_done(self):
        server = HushClawServer.__new__(HushClawServer)
        sid = "session-running"
        replay_done = json.dumps({"type": "done", "session_id": sid, "text": "authoritative"})
        entry = SimpleNamespace(
            text="partial answer",
            buffer=[],
            memory=SimpleNamespace(session_log=SimpleNamespace(session_wire_events=lambda _sid: [replay_done])),
            subscriber=None,
            is_running=lambda: True,
        )
        server._session_tasks = {sid: entry}
        ws = _MockWs()

        await server._subscribe_session(ws, sid)

        self.assertTrue(any(msg.get("type") == "done" for msg in ws.sent))
        self.assertFalse(any(msg.get("type") == "chunk" and msg.get("_replay") for msg in ws.sent))

    async def test_dispatch_rename_session(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            sid = "session-rename"
            mem.save_turn(sid, "user", "Investigate session naming")

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            server._os_api = None
            ws = _MockWs()

            await server._dispatch(ws, {
                "type": "rename_session",
                "session_id": sid,
                "title": "Important customer thread",
            })

            msg = ws.sent[-1]
            self.assertEqual(msg["type"], "session_renamed")
            self.assertTrue(msg["ok"])
            self.assertEqual(msg["session_id"], sid)
            self.assertEqual(msg["title"], "Important customer thread")
            item = next(i for i in mem.list_sessions(limit=10) if i["session_id"] == sid)
            self.assertEqual(item["title"], "Important customer thread")
            mem.close()

    async def test_dispatch_rename_session_validation_error(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            server._os_api = None
            ws = _MockWs()

            await server._dispatch(ws, {
                "type": "rename_session",
                "session_id": "missing-session",
                "title": "",
            })

            msg = ws.sent[-1]
            self.assertEqual(msg["type"], "session_renamed")
            self.assertFalse(msg["ok"])
            self.assertTrue(msg.get("error"))
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

    async def test_dispatch_list_profile_facts_surfaces_errors(self):
        server = HushClawServer.__new__(HushClawServer)

        class BrokenOS:
            def list_profile_facts(self, **_kwargs):
                raise RuntimeError("profile db unavailable")

        server._os_api = BrokenOS()
        ws = _MockWs()

        await server._dispatch(ws, {"type": "list_profile_facts"})

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "profile_facts")
        self.assertFalse(msg.get("ok"))
        self.assertEqual(msg.get("items"), [])
        self.assertIn("profile db unavailable", msg.get("error", ""))

    async def test_dispatch_list_todos_supports_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            for idx in range(3):
                mem.add_todo(f"Todo {idx}")

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_todos", "limit": 2, "offset": 0})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "todos")
            self.assertEqual(len(msg.get("items", [])), 2)
            self.assertEqual(msg.get("limit"), 2)
            self.assertEqual(msg.get("offset"), 0)
            self.assertTrue(msg.get("has_more"))
            mem.close()

    async def test_dispatch_insights_create_list_delete(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {
                "type": "create_insight",
                "text": "A durable interface removes choices, not power.",
            })
            created = ws.sent[-1]
            self.assertEqual(created.get("type"), "insight_created")
            item = created.get("item") or {}
            self.assertEqual(item.get("note_type"), "belief")
            self.assertEqual(item.get("memory_kind"), "user_model")
            self.assertEqual(item.get("source_type"), "curated")
            self.assertIn("insight", item.get("tags") or [])
            memory_note_id = mem.remember(
                "A strong product remembers fewer, sharper things.",
                title="Sharp memory",
                tags=["_auto_extract"],
                note_type="interest",
                memory_kind="user_model",
            )

            await server._dispatch(ws, {"type": "list_insights", "limit": 5, "offset": 0})
            listed = ws.sent[-1]
            self.assertEqual(listed.get("type"), "insights")
            self.assertEqual(listed.get("view"), "curated")
            self.assertEqual(len(listed.get("items", [])), 1)
            by_id = {entry.get("note_id"): entry for entry in listed.get("items", [])}
            self.assertEqual(by_id[item.get("note_id")].get("source_type"), "curated")

            await server._dispatch(ws, {"type": "list_insights", "view": "suggested", "limit": 5, "offset": 0})
            suggested = ws.sent[-1]
            self.assertEqual(suggested.get("view"), "suggested")
            by_id = {entry.get("note_id"): entry for entry in suggested.get("items", [])}
            self.assertEqual(by_id[memory_note_id].get("source_type"), "memory")

            await server._dispatch(ws, {"type": "delete_insight", "note_id": item.get("note_id")})
            deleted = ws.sent[-1]
            self.assertEqual(deleted.get("type"), "insight_deleted")
            self.assertTrue(deleted.get("ok"))
            mem.close()

    async def test_dispatch_insight_cleanup_preview_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            junk_id = mem.remember(
                "哪里需要补充",
                title="Auto: fragment",
                tags=["_auto_extract"],
                note_type="interest",
                memory_kind="user_model",
            )
            review_id = mem.remember(
                "用户认为产品体验应该通过更少层级和更强质感提升长期使用意愿。",
                title="Auto: product belief",
                tags=["_auto_extract"],
                note_type="belief",
                memory_kind="user_model",
            )

            await server._dispatch(ws, {"type": "preview_insight_cleanup", "limit": 10})
            preview = ws.sent[-1]
            self.assertEqual(preview.get("type"), "insight_cleanup_preview")
            self.assertEqual(preview.get("auto_delete_candidates")[0].get("note_id"), junk_id)
            self.assertEqual(preview.get("review_candidates")[0].get("note_id"), review_id)

            await server._dispatch(ws, {
                "type": "apply_insight_cleanup",
                "auto_delete_ids": [junk_id],
                "promote_ids": [review_id],
            })
            applied = ws.sent[-1]
            self.assertEqual(applied.get("type"), "insight_cleanup_applied")
            self.assertEqual(applied.get("deleted"), 1)
            self.assertEqual(applied.get("promoted"), 1)
            self.assertIsNone(mem.get_note(junk_id))
            promoted = mem.get_note(review_id)
            tags = json.loads(promoted.get("tags") or "[]")
            self.assertIn("insight", tags)
            mem.close()

    async def test_dispatch_list_profile_facts_returns_payloads(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.user_profile.upsert_fact(
                category="communication_style",
                key="direct",
                value={"summary": "prefers direct, pragmatic answers"},
                confidence=0.9,
                source_session_id="s-1",
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_profile_facts"})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "profile_facts")
            self.assertTrue(msg.get("ok"))
            self.assertEqual(len(msg.get("items", [])), 1)
            self.assertEqual(msg["items"][0]["value"], "prefers direct, pragmatic answers")
            self.assertEqual(msg["items"][0]["source"]["session_id"], "s-1")
            self.assertEqual(msg.get("total"), 1)
            self.assertFalse(msg.get("has_more"))
            mem.close()

    async def test_dispatch_list_profile_facts_supports_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.user_profile.upsert_fact(
                category="communication_style",
                key="direct",
                value={"summary": "prefers direct answers"},
                confidence=0.9,
            )
            mem.user_profile.upsert_fact(
                category="workflow_habits",
                key="tests",
                value={"summary": "expects tests before commits"},
                confidence=0.8,
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_profile_facts", "limit": 1, "offset": 0})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "profile_facts")
            self.assertEqual(len(msg.get("items", [])), 1)
            self.assertEqual(msg.get("total"), 2)
            self.assertEqual(msg.get("limit"), 1)
            self.assertEqual(msg.get("offset"), 0)
            self.assertTrue(msg.get("has_more"))
            mem.close()

    async def test_dispatch_list_belief_models_surfaces_errors(self):
        server = HushClawServer.__new__(HushClawServer)

        class BrokenOS:
            def list_belief_models(self, scopes=None):
                raise RuntimeError("belief db unavailable")

        server._os_api = BrokenOS()
        ws = _MockWs()

        await server._dispatch(ws, {"type": "list_belief_models"})

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "belief_models")
        self.assertFalse(msg.get("ok"))
        self.assertEqual(msg.get("items"), [])
        self.assertIn("belief db unavailable", msg.get("error", ""))

    async def test_dispatch_list_belief_models_returns_payloads(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            mem.remember(
                "工程质量需要用测试和边界条件证明。",
                title="Engineering belief",
                tags=["domain:engineering"],
                note_type="belief",
                memory_kind="user_model",
            )
            mem.save_belief_model_consolidation(
                domain="engineering",
                scope="global",
                current_stance="Engineering quality must be proven with tests and boundary cases.",
                summary="User evaluates engineering quality through evidence.",
                trajectory="Stable evidence-first engineering stance.",
                change_drivers=["boundary case failures"],
                signals=["tests", "edge cases"],
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_belief_models"})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "belief_models")
            self.assertTrue(msg.get("ok"))
            self.assertEqual(len(msg.get("items", [])), 1)
            self.assertEqual(msg["items"][0]["domain"], "engineering")
            self.assertEqual(
                msg["items"][0]["current_stance"],
                "Engineering quality must be proven with tests and boundary cases.",
            )
            self.assertEqual(msg["items"][0]["change_drivers"], ["boundary case failures"])
            self.assertEqual(msg["items"][0]["display_domain"], "engineering")
            self.assertTrue(msg["items"][0]["entries"][0]["source"]["note_id"])
            mem.close()

    async def test_dispatch_get_belief_model_returns_paginated_entries(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            for idx in range(3):
                mem.remember(
                    f"Engineering evidence {idx}",
                    title=f"Engineering belief {idx}",
                    tags=["domain:engineering"],
                    note_type="belief",
                    memory_kind="user_model",
                )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {
                "type": "get_belief_model",
                "domain": "engineering",
                "scope": "global",
                "entry_limit": 2,
                "entry_offset": 0,
            })

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "belief_model_detail")
            self.assertTrue(msg.get("ok"))
            item = msg.get("item") or {}
            self.assertEqual(item.get("domain"), "engineering")
            self.assertEqual(item.get("entry_count"), 3)
            self.assertEqual(item.get("entry_limit"), 2)
            self.assertEqual(item.get("entry_offset"), 0)
            self.assertTrue(item.get("entries_has_more"))
            self.assertEqual(len(item.get("entries", [])), 2)
            mem.close()

    async def test_dispatch_list_opinion_threads_returns_paginated_payloads(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            for idx in range(2):
                mem.upsert_opinion_event(
                    topic=f"memory evolution {idx}",
                    domain="memory-system",
                    event_type="new",
                    stance_delta=f"Stance {idx}",
                    evidence="User wants opinions to evolve over time.",
                    reason="Opinion timeline test.",
                    source_session_id=f"ses-{idx}",
                )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {"type": "list_opinion_threads", "limit": 1, "offset": 0})

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "opinion_threads")
            self.assertTrue(msg.get("ok"))
            self.assertEqual(msg.get("total"), 2)
            self.assertEqual(msg.get("limit"), 1)
            self.assertTrue(msg.get("has_more"))
            self.assertEqual(len(msg.get("items", [])), 1)
            self.assertEqual(msg["items"][0].get("events"), [])
            mem.close()

    async def test_dispatch_get_opinion_thread_returns_events_with_sources(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemoryStore(Path(d))
            created = mem.upsert_opinion_event(
                topic="memory evolution",
                domain="memory-system",
                event_type="new",
                stance_delta="Memory should track evolving views.",
                evidence="User wants to see thinking changes.",
                reason="Fragments lose trajectory.",
                source_session_id="ses-opinion",
            )
            mem.upsert_opinion_event(
                topic="memory evolution",
                domain="memory-system",
                event_type="reinforce",
                stance_delta="LLM interpretation should power the model.",
                evidence="User says this understanding must go through LLM.",
                reason="Semantic interpretation is required.",
                source_session_id="ses-opinion",
            )

            server = HushClawServer.__new__(HushClawServer)
            server._gateway = SimpleNamespace(memory=mem)
            ws = _MockWs()

            await server._dispatch(ws, {
                "type": "get_opinion_thread",
                "thread_id": created["thread_id"],
                "event_limit": 1,
                "event_offset": 0,
            })

            msg = ws.sent[-1]
            self.assertEqual(msg.get("type"), "opinion_thread_detail")
            self.assertTrue(msg.get("ok"))
            item = msg.get("item") or {}
            self.assertEqual(item.get("thread_id"), created["thread_id"])
            self.assertEqual(item.get("event_count"), 2)
            self.assertEqual(item.get("event_limit"), 1)
            self.assertTrue(item.get("events_has_more"))
            self.assertEqual(len(item.get("events", [])), 1)
            self.assertEqual(item["events"][0]["source"]["session_id"], "ses-opinion")
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
