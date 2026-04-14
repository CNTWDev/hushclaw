"""Write a user skill to a SKILL.md file on disk."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _bump_patch(version: str) -> str:
    """Increment the patch segment of a semver string ("1.2.3" → "1.2.4").

    If the string is not parseable as X.Y.Z the original value is returned
    unchanged so callers never get an unexpected crash.
    """
    parts = version.strip().split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        return version
    return ".".join(parts)


def _read_frontmatter_field(lines: list[str], key: str) -> str:
    """Return the value of *key* from a SKILL.md frontmatter line list."""
    prefix = f"{key}:"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip().strip('"').strip("'")
    return ""


def _append_changelog(skill_dir: Path, version: str, is_new: bool) -> None:
    """Create or prepend an entry to *skill_dir*/CHANGELOG.md.

    Each entry is a one-line ``## version — date`` heading.
    """
    today = date.today().isoformat()
    label = "Initial creation" if is_new else "Updated"
    new_entry = f"## {version} — {today}\n{label}\n"

    changelog_path = skill_dir / "CHANGELOG.md"
    if changelog_path.exists():
        try:
            existing = changelog_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        # Remove old header line if present so we don't duplicate it.
        body = existing.lstrip()
        if body.startswith("# Changelog"):
            body = body[len("# Changelog"):].lstrip("\n")
        content = f"# Changelog\n\n{new_entry}\n{body}".rstrip() + "\n"
    else:
        content = f"# Changelog\n\n{new_entry}"

    try:
        changelog_path.write_text(content, encoding="utf-8")
    except OSError:
        pass  # best-effort; never block the skill write


def write_skill(
    name: str,
    content: str,
    description: str = "",
    skill_dir: "Path | str | None" = None,
    source: str = "user_created",
) -> Path:
    """Write a SKILL.md file to *skill_dir*/{slug}/SKILL.md and return its path.

    The file is created (or overwritten) immediately — no registry reload is
    performed here; callers should call ``registry.reload()`` afterwards so the
    new skill is available without a server restart.

    Frontmatter fields preserved on update (overwrite):
    - created_at  — ISO date of first write
    - status      — lifecycle state ("draft" → "validated" etc.)
    - usage_count — telemetry counter
    - version     — auto-bumped (patch) on every update; "1.0.0" on creation

    A CHANGELOG.md is maintained alongside SKILL.md, prepended on each save.
    """
    if skill_dir is None:
        raise ValueError("skill_dir must be provided")
    skill_dir = Path(skill_dir)
    slug = _slugify(name)
    skill_path = skill_dir / slug / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)

    is_new = not skill_path.exists()

    # ── Read fields to preserve from the existing file ────────────────────
    created_at = date.today().isoformat()
    version = "1.0.0"
    status = "draft"
    usage_count = "0"

    if not is_new:
        try:
            existing = skill_path.read_text(encoding="utf-8")
            fm_lines: list[str] = []
            if existing.startswith("---"):
                parts = existing.split("---", 2)
                if len(parts) >= 2:
                    fm_lines = parts[1].splitlines()

            preserved_at = _read_frontmatter_field(fm_lines, "created_at")
            if preserved_at:
                created_at = preserved_at

            old_version = _read_frontmatter_field(fm_lines, "version")
            if old_version:
                version = _bump_patch(old_version)

            preserved_status = _read_frontmatter_field(fm_lines, "status")
            if preserved_status:
                status = preserved_status

            preserved_usage = _read_frontmatter_field(fm_lines, "usage_count")
            if preserved_usage:
                usage_count = preserved_usage
        except OSError:
            pass

    fm_description = description.replace("\n", " ") if description else name
    skill_path.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: {fm_description}\n"
        f"author: user\n"
        f'version: "{version}"\n'
        f"status: {status}\n"
        f"source: {source}\n"
        f"created_at: {created_at}\n"
        f"usage_count: {usage_count}\n"
        f"---\n\n"
        f"{content.strip()}\n",
        encoding="utf-8",
    )

    _append_changelog(skill_path.parent, version, is_new)

    return skill_path
