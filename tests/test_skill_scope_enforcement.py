from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hushclaw.skills.installer import SkillInstaller
from hushclaw.skills.loader import SkillRegistry
from hushclaw.skills.manager import SkillManager
from hushclaw.skills.validator import SkillValidator


def _write_skill(root: Path, slug: str, name: str | None = None) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name or slug}\ndescription: test\n---\n\nBody\n",
        encoding="utf-8",
    )


class TestSkillScopeEnforcement(unittest.TestCase):
    def test_registry_delete_rejects_non_user_tiers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system_dir = root / "system-skills"
            user_dir = root / "user-skills"
            workspace_dir = root / "workspace-skills"
            _write_skill(system_dir, "sys-only")
            _write_skill(user_dir, "user-only")
            _write_skill(workspace_dir, "ws-only")

            registry = SkillRegistry([
                (system_dir, "system"),
                (user_dir, "user"),
                (workspace_dir, "workspace"),
            ])

            ok, err = registry.delete_skill("sys-only")
            self.assertFalse(ok)
            self.assertIn("Cannot delete system skill", err)

            ok, err = registry.delete_skill("ws-only")
            self.assertFalse(ok)
            self.assertIn("Cannot delete workspace skill", err)

            ok, err = registry.delete_skill("user-only")
            self.assertTrue(ok)
            self.assertEqual(err, "")

    def test_skill_manager_edit_and_patch_reject_non_user_tiers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system_dir = root / "system-skills"
            user_dir = root / "user-skills"
            workspace_dir = root / "workspace-skills"
            _write_skill(system_dir, "sys-only")
            _write_skill(user_dir, "user-only")
            _write_skill(workspace_dir, "ws-only")

            registry = SkillRegistry([
                (system_dir, "system"),
                (user_dir, "user"),
                (workspace_dir, "workspace"),
            ])
            manager = SkillManager(
                registry=registry,
                installer=SkillInstaller(),
                validator=SkillValidator(),
                install_dir=user_dir,
            )

            with self.assertRaisesRegex(ValueError, "Cannot edit system skill 'sys-only'"):
                manager.edit("sys-only", "updated")

            with self.assertRaisesRegex(ValueError, "Cannot patch workspace skill 'ws-only'"):
                manager.patch("ws-only", "refine")

            path = manager.edit("user-only", "updated")
            self.assertTrue(path.exists())
