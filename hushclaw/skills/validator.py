"""SkillValidator: SKILL.md format validation and compatibility checking.

This module is the single source of truth for:
- Parsing SKILL.md frontmatter fields
- Validating that a SKILL.md is well-formed
- Checking OS / binary / env-var compatibility at install time

Used by: SkillInstaller, SkillManager, and potentially the WebUI handler.
Not used by loader.py's _parse() — that has its own inline parsing for load-time
checks, keeping the hot path dependency-free.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationResult:
    """Result of validating a SKILL.md file."""
    valid: bool
    name: str = ""
    version: str = ""
    description: str = ""
    errors: list[str] = field(default_factory=list)    # blocking — install should fail
    warnings: list[str] = field(default_factory=list)  # non-blocking — install proceeds

    @property
    def ok(self) -> bool:
        return self.valid and not self.errors


@dataclass
class CompatResult:
    """Result of a compatibility check against the current environment."""
    compatible: bool
    warnings: list[str] = field(default_factory=list)   # non-blocking (missing bin/env)
    blocking: list[str] = field(default_factory=list)   # blocking (OS mismatch)

    @property
    def all_warnings(self) -> list[str]:
        return self.blocking + self.warnings


# ---------------------------------------------------------------------------
# SkillValidator
# ---------------------------------------------------------------------------

class SkillValidator:
    """Parse and validate SKILL.md files."""

    # ------------------------------------------------------------------
    # Frontmatter parsing
    # ------------------------------------------------------------------

    def parse_frontmatter(self, skill_md_path: Path) -> dict:
        """Parse all frontmatter fields from a SKILL.md file.

        Returns a flat dict with string values for simple scalar fields,
        list values for list fields, and the raw parsed metadata dict
        under ``_metadata``.  Returns empty dict on any I/O error.
        """
        result: dict = {}
        try:
            text = skill_md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return result

        if not text.startswith("---"):
            return result
        parts = text.split("---", 2)
        if len(parts) < 2:
            return result

        fm = parts[1]
        in_requires = False

        for line in fm.splitlines():
            ls = line.strip()

            # Legacy requires: block
            if ls == "requires:":
                in_requires = True
                continue
            if in_requires:
                if ls.startswith("bins:"):
                    result.setdefault("requires_bins", []).extend(
                        self._parse_yaml_list(ls[5:].strip())
                    )
                    continue
                elif ls.startswith("env:"):
                    result.setdefault("requires_env", []).extend(
                        self._parse_yaml_list(ls[4:].strip())
                    )
                    continue
                elif ls and ":" in ls and not line.startswith((" ", "\t")):
                    in_requires = False
                else:
                    continue

            # Scalar fields
            if ls.startswith("name:"):
                result["name"] = ls[5:].strip().strip('"').strip("'")
            elif ls.startswith("description:"):
                result["description"] = ls[12:].strip().strip('"').strip("'")
            elif ls.startswith("version:"):
                result["version"] = ls[8:].strip().strip('"').strip("'")
            elif ls.startswith("author:"):
                result["author"] = ls[7:].strip().strip('"').strip("'")
            elif ls.startswith("license:"):
                result["license"] = ls[8:].strip().strip('"').strip("'")
            elif ls.startswith("homepage:"):
                result["homepage"] = ls[9:].strip().strip('"').strip("'")
            elif ls.startswith("source:"):
                result["source"] = ls[7:].strip().strip('"').strip("'")
            elif ls.startswith("tags:"):
                result["tags"] = self._parse_yaml_list(ls[5:].strip())
            elif ls.startswith("include_files:"):
                result["include_files"] = self._parse_yaml_list(ls[14:].strip())
            elif ls.startswith("metadata:"):
                raw_json = ls[9:].strip()
                try:
                    result["_metadata"] = json.loads(raw_json)
                except (json.JSONDecodeError, ValueError):
                    pass

        # Extract bins/env/os from new-style metadata JSON
        meta = result.get("_metadata")
        if isinstance(meta, dict):
            openclaw = meta.get("openclaw") or meta.get("clawdbot") or {}
            if isinstance(openclaw, dict):
                requires = openclaw.get("requires", {})
                if isinstance(requires, dict):
                    raw_bins = requires.get("bins", [])
                    raw_env  = requires.get("env", [])
                    if isinstance(raw_bins, list):
                        result.setdefault("requires_bins", []).extend(str(b) for b in raw_bins)
                    if isinstance(raw_env, list):
                        result.setdefault("requires_env", []).extend(str(e) for e in raw_env)
                raw_os = openclaw.get("os", [])
                if isinstance(raw_os, list):
                    result["os_list"] = [str(o) for o in raw_os]
                raw_install = openclaw.get("install", [])
                if isinstance(raw_install, list):
                    result["install_specs"] = [s for s in raw_install if isinstance(s, dict)]

        # Deduplicate lists while preserving order
        for key in ("requires_bins", "requires_env"):
            if key in result:
                result[key] = list(dict.fromkeys(result[key]))

        return result

    @staticmethod
    def _parse_yaml_list(raw: str) -> list[str]:
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1]
            items = [s.strip().strip('"').strip("'") for s in inner.split(",")]
            return [i for i in items if i]
        return []

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, skill_md_path: Path) -> ValidationResult:
        """Check that the SKILL.md is minimally valid.

        A valid SKILL.md must:
        - Be readable
        - Start with ``---`` frontmatter
        - Contain a non-empty ``name:`` field
        """
        if not skill_md_path.exists():
            return ValidationResult(valid=False, errors=[f"SKILL.md not found: {skill_md_path}"])

        try:
            text = skill_md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return ValidationResult(valid=False, errors=[f"Cannot read SKILL.md: {exc}"])

        if not text.startswith("---"):
            return ValidationResult(
                valid=False,
                errors=["SKILL.md has no YAML frontmatter. Add --- name: <id> --- at the top."],
            )

        fm_dict = self.parse_frontmatter(skill_md_path)
        name    = fm_dict.get("name", "").strip()
        version = fm_dict.get("version", "")
        description = fm_dict.get("description", "")

        errors: list[str] = []
        warnings: list[str] = []

        if not name:
            errors.append("Missing required field: name. Add `name: my-skill` to the frontmatter.")

        return ValidationResult(
            valid=not errors,
            name=name,
            version=version,
            description=description,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Compatibility
    # ------------------------------------------------------------------

    def check_compatibility(self, skill_md_path: Path) -> CompatResult:
        """Check the current environment against the skill's declared requirements.

        Returns a CompatResult with:
        - ``blocking`` — OS mismatch (install may still proceed, but skill won't work)
        - ``warnings`` — missing binary or env var (user action needed before using)
        """
        fm = self.parse_frontmatter(skill_md_path)
        os_list     = fm.get("os_list", [])
        bins        = fm.get("requires_bins", [])
        env_vars    = fm.get("requires_env", [])

        blocking: list[str] = []
        warnings: list[str] = []

        if os_list and sys.platform not in os_list:
            blocking.append(
                f"OS mismatch: skill requires {os_list}, "
                f"this machine is {sys.platform!r}. "
                "The skill may not function correctly on this platform."
            )

        for b in bins:
            if shutil.which(b) is None:
                warnings.append(
                    f"Missing binary: {b!r} is not found on PATH. "
                    f"Install it before using this skill (e.g. `brew install {b}` on macOS)."
                )

        for e in env_vars:
            if not os.environ.get(e):
                warnings.append(
                    f"Missing env var: {e!r} is not set. "
                    "Add it to your shell config or hushclaw environment."
                )

        compatible = not blocking
        return CompatResult(compatible=compatible, warnings=warnings, blocking=blocking)
