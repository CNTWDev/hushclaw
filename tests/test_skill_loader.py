import json
from pathlib import Path

from hushclaw.skills.loader import SkillRegistry


def _write_skill(path: Path, name: str, description: str = "Demo") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )


def test_marketplace_bundle_prefers_plugin_skill_roots(tmp_path):
    bundle = tmp_path / "frontend-slides"
    _write_skill(bundle / "SKILL.md", "frontend-slides", "Wrapper skill")
    (bundle / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (bundle / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({
            "plugins": [
                {"source": "./plugins/frontend-slides"},
            ]
        }),
        encoding="utf-8",
    )
    plugin_root = bundle / "plugins" / "frontend-slides"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"skills": "./skills/"}),
        encoding="utf-8",
    )
    _write_skill(
        plugin_root / "skills" / "frontend-slides" / "SKILL.md",
        "frontend-slides",
        "Plugin skill",
    )

    registry = SkillRegistry([(tmp_path, "user")])
    skill = registry.get("frontend-slides")

    assert skill is not None
    assert skill["path"].endswith("plugins/frontend-slides/skills/frontend-slides/SKILL.md")
    assert len(registry._skill_versions["frontend-slides"]) == 1


def test_override_governance_marks_prunable_shadowed_copy(tmp_path):
    system_dir = tmp_path / "system"
    user_dir = tmp_path / "user"
    _write_skill(system_dir / "demo" / "SKILL.md", "demo", "System skill")
    _write_skill(user_dir / "demo" / "SKILL.md", "demo", "User skill")

    registry = SkillRegistry([(system_dir, "system"), (user_dir, "user")])
    item = registry.detail("demo")

    assert item is not None
    governance = item["governance"]
    assert governance["needs_governance"] is True
    assert governance["prunable_shadow_count"] == 0
    assert governance["blocked_shadow_count"] == 1


def test_prune_shadowed_removes_separate_user_shadow_copy(tmp_path):
    first_dir = tmp_path / "user-a"
    second_dir = tmp_path / "user-b"
    _write_skill(first_dir / "demo" / "SKILL.md", "demo", "Shadowed")
    _write_skill(second_dir / "demo" / "SKILL.md", "demo", "Active")

    registry = SkillRegistry([(first_dir, "user"), (second_dir, "workspace")])
    ok, error, removed = registry.prune_shadowed("demo")

    assert ok is True
    assert error == ""
    assert removed
    assert not (first_dir / "demo").exists()
