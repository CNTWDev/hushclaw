"""Tests for server slash-command handling with prompt-only skills."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

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
