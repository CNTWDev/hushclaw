from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from hushclaw.server.skill_handler import (
    build_agent_runtime_status,
    handle_check_skills_health,
    handle_get_agent_runtime_status,
    handle_export_skills,
    handle_get_skill_detail,
    handle_import_skill_zip,
    handle_install_skill_repo,
    handle_install_skill_zip,
    handle_list_skills,
    handle_save_skill,
    handle_set_skill_enabled,
)
from hushclaw.skills.installer import InstallResult
from hushclaw.skills.loader import SkillRegistry


class _MockWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class _FakeRegistry:
    def __init__(self):
        self.reload_count = 0

    def reload(self):
        self.reload_count += 1

    def list_all(self):
        return []


class _ToolDef:
    def __init__(self, name: str):
        self.name = name


class _AgentToolRegistry:
    def __init__(self, names: list[str]):
        self._names = names

    def list_tools(self):
        return [_ToolDef(name) for name in self._names]


class _AgentRuntimeStatusGateway:
    def __init__(self, user_skill_dir: Path, tools: list[str], explicit_tools: list[str] | None = None):
        self.base_agent = SimpleNamespace(
            config=SimpleNamespace(
                tools=SimpleNamespace(skill_dir=None, user_skill_dir=user_skill_dir),
                agent=SimpleNamespace(workspace_dir=None),
            ),
        )
        self._tools = tools
        self._explicit_tools = explicit_tools if explicit_tools is not None else tools

    def get_agent_def(self, name: str):
        return {"name": name, "tools": self._explicit_tools}

    def get_pool(self, name: str):
        return SimpleNamespace(_agent=SimpleNamespace(registry=_AgentToolRegistry(self._tools)))


class TestSkillHandlerZipRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def test_save_skill_uses_skill_manager(self):
        with tempfile.TemporaryDirectory() as d:
            user_skill_dir = Path(d) / "user-skills"
            user_skill_dir.mkdir(parents=True)
            created_path = user_skill_dir / "demo-skill" / "SKILL.md"
            manager = SimpleNamespace(create=MagicMock(return_value=created_path))
            registry = _FakeRegistry()
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_manager=manager,
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=None, user_skill_dir=user_skill_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )
            ws = _MockWs()

            await handle_save_skill(
                ws,
                {
                    "name": "demo-skill",
                    "description": "Demo workflow",
                    "content": "## Workflow\n- Demo\n",
                },
                gateway,
            )

            manager.create.assert_called_once_with(
                name="demo-skill",
                content="## Workflow\n- Demo",
                description="Demo workflow",
            )
            self.assertEqual(ws.sent[0].get("type"), "skill_saved")
            self.assertTrue(ws.sent[0].get("ok"))
            self.assertEqual(ws.sent[0].get("path"), str(created_path))
            self.assertEqual(ws.sent[-1].get("type"), "skills")

    async def test_export_skills_includes_nested_support_files(self):
        with tempfile.TemporaryDirectory() as d:
            user_skill_dir = Path(d) / "user-skills"
            skill_dir = user_skill_dir / "my-skill"
            (skill_dir / "references").mkdir(parents=True)
            (skill_dir / "assets").mkdir()
            (skill_dir / "tools").mkdir()
            (skill_dir / "__pycache__").mkdir()

            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-skill\ndescription: Test skill\n---\n\nBody\n",
                encoding="utf-8",
            )
            (skill_dir / "references" / "context.md").write_text("ref", encoding="utf-8")
            (skill_dir / "assets" / "template.html").write_text("<html></html>", encoding="utf-8")
            (skill_dir / "tools" / "helper.py").write_text("print('ok')\n", encoding="utf-8")
            (skill_dir / "tools" / "schema.json").write_text('{"ok":true}\n', encoding="utf-8")
            (skill_dir / "__pycache__" / "helper.pyc").write_bytes(b"compiled")
            (skill_dir / ".DS_Store").write_text("junk", encoding="utf-8")

            registry = SkillRegistry([(user_skill_dir, "user")])
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=None, user_skill_dir=user_skill_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )
            ws = _MockWs()

            await handle_export_skills(ws, {"names": ["my-skill"]}, gateway)

            self.assertTrue(ws.sent)
            msg = ws.sent[-1]
            self.assertTrue(msg["ok"])
            zip_bytes = base64.b64decode(msg["data"])
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names = set(zf.namelist())

            self.assertIn("my-skill/SKILL.md", names)
            self.assertIn("my-skill/references/context.md", names)
            self.assertIn("my-skill/assets/template.html", names)
            self.assertIn("my-skill/tools/helper.py", names)
            self.assertIn("my-skill/tools/schema.json", names)
            self.assertNotIn("my-skill/__pycache__/helper.pyc", names)
            self.assertNotIn("my-skill/.DS_Store", names)

    async def test_export_all_only_includes_user_skills(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system_dir = root / "system-skills"
            user_dir = root / "user-skills"

            for parent, slug, name in (
                (system_dir, "sys-skill", "sys-skill"),
                (user_dir, "user-skill", "user-skill"),
            ):
                skill_dir = parent / slug
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: Test skill\n---\n\nBody\n",
                    encoding="utf-8",
                )

            registry = SkillRegistry([(system_dir, "system"), (user_dir, "user")])
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=system_dir, user_skill_dir=user_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )
            ws = _MockWs()

            await handle_export_skills(ws, {"names": []}, gateway)

            msg = ws.sent[-1]
            self.assertTrue(msg["ok"])
            zip_bytes = base64.b64decode(msg["data"])
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names = set(zf.namelist())

            self.assertIn("user-skill/SKILL.md", names)
            self.assertNotIn("sys-skill/SKILL.md", names)

    async def test_import_skill_zip_preserves_full_directory_from_wrapped_zip(self):
        with tempfile.TemporaryDirectory() as d:
            user_skill_dir = Path(d) / "user-skills"
            user_skill_dir.mkdir(parents=True)

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "release-bundle/my-skill/SKILL.md",
                    "---\nname: my-skill\ndescription: Test skill\n---\n\nBody\n",
                )
                zf.writestr("release-bundle/my-skill/references/context.md", "ref")
                zf.writestr("release-bundle/my-skill/assets/template.html", "<html></html>")
                zf.writestr("release-bundle/my-skill/tools/helper.py", "print('ok')\n")
                zf.writestr("release-bundle/my-skill/tools/schema.json", '{"ok":true}\n')

            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    config=SimpleNamespace(tools=SimpleNamespace(user_skill_dir=user_skill_dir)),
                    registry=MagicMock(),
                    _skill_registry=None,
                )
            )
            ws = _MockWs()

            with patch(
                "hushclaw.skills.installer.SkillInstaller.post_install",
                new=AsyncMock(return_value=InstallResult(ok=True, slug="my-skill")),
            ):
                await handle_import_skill_zip(
                    ws,
                    {
                        "filename": "wrapped-skill.zip",
                        "data": base64.b64encode(buf.getvalue()).decode(),
                    },
                    gateway,
                )

            target_dir = user_skill_dir / "my-skill"
            self.assertTrue((target_dir / "SKILL.md").exists())
            self.assertTrue((target_dir / "references" / "context.md").exists())
            self.assertTrue((target_dir / "assets" / "template.html").exists())
            self.assertTrue((target_dir / "tools" / "helper.py").exists())
            self.assertTrue((target_dir / "tools" / "schema.json").exists())

            self.assertTrue(ws.sent)
            result = ws.sent[-1]
            self.assertEqual(result.get("type"), "skill_import_result")
            self.assertTrue(result.get("ok"))
            self.assertIn("my-skill", result.get("installed", []))

    async def test_install_skill_repo_returns_structured_error_when_skill_dir_unwritable(self):
        skill_dir = SimpleNamespace(mkdir=MagicMock(side_effect=PermissionError("denied")))
        gateway = SimpleNamespace(
            base_agent=SimpleNamespace(
                config=SimpleNamespace(tools=SimpleNamespace(user_skill_dir=skill_dir)),
            )
        )
        ws = _MockWs()

        await handle_install_skill_repo(ws, {"url": "https://github.com/example/demo-skill.git"}, gateway)

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "skill_install_result")
        self.assertFalse(msg.get("ok"))
        self.assertEqual(msg.get("url"), "https://github.com/example/demo-skill.git")
        self.assertIn("denied", msg.get("error", ""))

    async def test_install_skill_zip_returns_structured_error_when_skill_dir_unwritable(self):
        skill_dir = SimpleNamespace(mkdir=MagicMock(side_effect=PermissionError("denied")))
        gateway = SimpleNamespace(
            base_agent=SimpleNamespace(
                config=SimpleNamespace(tools=SimpleNamespace(user_skill_dir=skill_dir)),
            )
        )
        ws = _MockWs()

        await handle_install_skill_zip(
            ws,
            {"url": "https://example.com/demo-skill.zip", "slug": "demo-skill"},
            gateway,
        )

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "skill_install_result")
        self.assertFalse(msg.get("ok"))
        self.assertEqual(msg.get("url"), "https://example.com/demo-skill.zip")
        self.assertIn("denied", msg.get("error", ""))

    async def test_import_skill_zip_returns_structured_error_when_skill_dir_unwritable(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("demo-skill/SKILL.md", "---\nname: demo-skill\n---\n\nBody\n")
        skill_dir = SimpleNamespace(mkdir=MagicMock(side_effect=PermissionError("denied")))
        gateway = SimpleNamespace(
            base_agent=SimpleNamespace(
                config=SimpleNamespace(tools=SimpleNamespace(user_skill_dir=skill_dir)),
            )
        )
        ws = _MockWs()

        await handle_import_skill_zip(
            ws,
            {
                "filename": "demo-skill.zip",
                "data": base64.b64encode(buf.getvalue()).decode(),
            },
            gateway,
        )

        msg = ws.sent[-1]
        self.assertEqual(msg.get("type"), "skill_import_result")
        self.assertFalse(msg.get("ok"))
        self.assertIn("denied", msg.get("error", ""))


class TestSkillLibraryIndex(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_filters_and_exposes_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system_dir = root / "system"
            user_dir = root / "user"
            for parent, desc in ((system_dir, "System version"), (user_dir, "User version")):
                skill_dir = parent / "dupe"
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: dupe\ndescription: {desc}\ntags: [review]\n---\n\nBody\n",
                    encoding="utf-8",
                )
            registry = SkillRegistry([(system_dir, "system"), (user_dir, "user")])
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=system_dir, user_skill_dir=user_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )
            ws = _MockWs()

            await handle_list_skills(ws, gateway, {"q": "dupe", "status": "conflicts"})

            msg = ws.sent[-1]
            self.assertEqual(msg["type"], "skills")
            self.assertEqual(msg["total"], 1)
            self.assertTrue(msg["items"][0]["has_conflict"])
            self.assertEqual(msg["items"][0]["scope"], "user")

    async def test_skill_detail_and_health_report(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            skill_dir = user_dir / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\nrequires:\n  env: [MISSING_HC_TEST_TOKEN]\n---\n\nBody\n",
                encoding="utf-8",
            )
            registry = SkillRegistry([(user_dir, "user")])
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=None, user_skill_dir=user_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )

            ws = _MockWs()
            await handle_get_skill_detail(ws, {"name": "demo"}, gateway)
            self.assertTrue(ws.sent[-1]["ok"])
            self.assertIn("content_preview", ws.sent[-1]["item"])

            await handle_check_skills_health(ws, gateway)
            report = ws.sent[-1]
            self.assertEqual(report["type"], "skills_health")
            self.assertEqual(report["summary"]["issues"], 1)
            self.assertFalse(report["items"][0]["ok"])

    async def test_set_skill_enabled_hides_skill_from_runtime_availability(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            skill_dir = user_dir / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\n\nBody\n",
                encoding="utf-8",
            )
            registry = SkillRegistry([(user_dir, "user")])
            gateway = SimpleNamespace(
                base_agent=SimpleNamespace(
                    _skill_registry=registry,
                    config=SimpleNamespace(
                        tools=SimpleNamespace(skill_dir=None, user_skill_dir=user_dir),
                        agent=SimpleNamespace(workspace_dir=None),
                    ),
                )
            )
            ws = _MockWs()

            await handle_set_skill_enabled(ws, {"name": "demo", "enabled": False}, gateway)

            result = ws.sent[0]
            self.assertTrue(result["ok"])
            self.assertFalse(result["item"]["enabled"])
            self.assertTrue((user_dir / ".skill-state.json").exists())
            self.assertFalse(registry.get("demo")["available"])


class TestAgentRuntimeStatus(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_status_reports_dynamic_skill_access(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            gateway = _AgentRuntimeStatusGateway(user_dir, ["list_skills", "use_skill"], ["list_skills", "use_skill"])

            payload = build_agent_runtime_status(gateway, "default")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["type"], "agent_runtime_status")
            self.assertTrue(payload["can_load_skills"])
            self.assertTrue(payload["can_discover_skills"])
            self.assertEqual(payload["skill_loader_tools"], ["use_skill"])
            self.assertEqual(payload["skill_discovery_tools"], ["list_skills"])
            self.assertEqual(payload["warnings"], [])

    async def test_runtime_status_accepts_search_skills_for_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            gateway = _AgentRuntimeStatusGateway(user_dir, ["search_skills", "use_skill"], ["search_skills", "use_skill"])

            payload = build_agent_runtime_status(gateway, "writer")

            self.assertTrue(payload["can_load_skills"])
            self.assertTrue(payload["can_discover_skills"])
            self.assertEqual(payload["skill_discovery_tools"], ["search_skills"])
            self.assertEqual(payload["warnings"], [])

    async def test_runtime_status_warns_when_custom_tools_block_skills(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            gateway = _AgentRuntimeStatusGateway(user_dir, ["recall"], ["recall"])

            payload = build_agent_runtime_status(gateway, "writer")

            self.assertFalse(payload["inherits_global_tools"])
            self.assertFalse(payload["can_load_skills"])
            self.assertEqual(payload["custom_tools"], ["recall"])
            self.assertIn("use_skill", payload["warnings"][0])

    async def test_runtime_status_warns_when_skill_discovery_missing(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            gateway = _AgentRuntimeStatusGateway(user_dir, ["use_skill"], ["use_skill"])

            payload = build_agent_runtime_status(gateway, "writer")

            self.assertTrue(payload["can_load_skills"])
            self.assertFalse(payload["can_discover_skills"])
            self.assertIn("search_skills or list_skills", payload["warnings"][0])

    async def test_runtime_status_handler_payload(self):
        with tempfile.TemporaryDirectory() as d:
            user_dir = Path(d) / "user"
            gateway = _AgentRuntimeStatusGateway(user_dir, ["list_skills", "skill_view"], [])
            ws = _MockWs()

            await handle_get_agent_runtime_status(ws, {"name": "default"}, gateway)

            payload = ws.sent[-1]
            self.assertEqual(payload["type"], "agent_runtime_status")
            self.assertTrue(payload["inherits_global_tools"])
            self.assertTrue(payload["can_load_skills"])
            self.assertEqual(payload["effective_tool_count"], 2)
