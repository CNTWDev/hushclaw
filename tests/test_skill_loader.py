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
