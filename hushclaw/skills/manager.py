"""SkillManager: unified façade for all skill operations.

Injected as ``_skill_manager`` into the agent executor context so tools can
install, create, and delete skills without importing server-layer modules.

Usage in tools:
    async def install_skill(source: str, _skill_manager=None) -> ToolResult:
        result = await _skill_manager.install(source)
        ...

    def remember_skill(name: str, content: str, _skill_manager=None) -> ToolResult:
        path = _skill_manager.create(name, content)
        ...
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from hushclaw.skills.installer import InstallResult, SkillInstaller
from hushclaw.skills.loader import SkillRegistry
from hushclaw.skills.validator import SkillValidator
from hushclaw.skills.writer import write_skill

if TYPE_CHECKING:
    pass

log = logging.getLogger("hushclaw.skills.manager")


class SkillManager:
    """Single entry point for all skill state-changing operations.

    Read-only queries (use_skill, list_skills) can use ``_skill_registry``
    directly for simplicity.  All writes go through this manager.
    """

    def __init__(
        self,
        registry: "SkillRegistry | None",
        installer: SkillInstaller,
        validator: SkillValidator,
        install_dir: "Path | None",
        tool_registry=None,
        gateway=None,
        workspace_install_dir: "Path | None" = None,
    ) -> None:
        self.registry    = registry
        self.installer   = installer
        self.validator   = validator
        self.install_dir = install_dir
        self.workspace_install_dir = workspace_install_dir
        self._tool_registry = tool_registry
        self._gateway       = gateway

    # ------------------------------------------------------------------
    # Gateway binding (set after agent loop is created with a gateway)
    # ------------------------------------------------------------------

    def set_gateway(self, gateway) -> None:
        """Bind (or rebind) the gateway so post_install can clear cached loops."""
        self._gateway = gateway

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    async def install(
        self,
        source: str,
        slug: str | None = None,
        on_progress: "Callable[[str], Awaitable] | None" = None,
        tier: str = "user",
    ) -> InstallResult:
        """Install a skill from *source* (local path, local ZIP, HTTPS ZIP, or Git URL).

        Delegates to SkillInstaller with the manager's registry, tool_registry,
        gateway, and install_dir all pre-wired.

        Pass ``tier='workspace'`` to install into the active workspace's skill
        directory instead of the user-level directory.
        """
        target_dir = self.workspace_install_dir if tier == "workspace" else self.install_dir
        if not target_dir:
            label = "workspace" if tier == "workspace" else "user"
            return InstallResult(
                ok=False,
                error=(
                    f"No {label} skill directory configured. "
                    + ("Ensure a workspace with a skills/ subdirectory is active."
                       if tier == "workspace"
                       else "Set tools.user_skill_dir in hushclaw.toml.")
                ),
            )
        return await self.installer.install(
            source=source,
            install_dir=target_dir,
            slug=slug or None,
            skill_registry=self.registry,
            tool_registry=self._tool_registry,
            gateway=self._gateway,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------
    # Create from text (chat-driven skill authoring)
    # ------------------------------------------------------------------

    def create(self, name: str, content: str, description: str = "", tier: str = "user") -> Path:
        """Write a skill from plain text and reload the registry.

        Returns the path to the written SKILL.md.
        Pass ``tier='workspace'`` to write into the active workspace's skill
        directory instead of the user-level directory.
        Raises ``ValueError`` if no install directory is configured.
        """
        target_dir = self.workspace_install_dir if tier == "workspace" else self.install_dir
        if not target_dir:
            label = "workspace" if tier == "workspace" else "user"
            raise ValueError(
                f"No {label} skill directory configured. "
                + ("Ensure a workspace with a skills/ subdirectory is active."
                   if tier == "workspace"
                   else "Set tools.user_skill_dir in hushclaw.toml.")
            )
        path = write_skill(name=name, content=content, description=description,
                           skill_dir=target_dir)
        if self.registry is not None:
            self.registry.reload()
        return path

    def edit(self, name: str, content: str, description: str = "") -> Path:
        """Rewrite an existing editable skill while preserving frontmatter history."""
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"Skill '{name}' not found")
        tier = str(skill.get("tier") or "user")
        if tier != "user":
            raise ValueError(f"Cannot edit {tier} skill '{name}'")
        skill_path = Path(str(skill["path"]))
        skill_dir = skill_path.parent.parent
        path = write_skill(
            name=name,
            content=content,
            description=description or str(skill.get("description") or name),
            skill_dir=skill_dir,
            source=str(skill.get("source") or "user_edited"),
        )
        if self.registry is not None:
            self.registry.reload()
        return path

    def patch(self, name: str, patch_instructions: str) -> Path:
        """Append refinement notes to an existing user skill."""
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"Skill '{name}' not found")
        tier = str(skill.get("tier") or "user")
        if tier != "user":
            raise ValueError(f"Cannot patch {tier} skill '{name}'")
        skill_path = Path(str(skill["path"]))
        existing = skill_path.read_text(encoding="utf-8", errors="ignore")
        if existing.startswith("---"):
            parts = existing.split("---", 2)
            body = parts[2].strip() if len(parts) >= 3 else existing
        else:
            body = existing
        refined = (
            body.rstrip()
            + "\n\n## Refinements\n"
            + f"- {patch_instructions.strip()}\n"
        )
        return self.edit(
            name=name,
            content=refined,
            description=str(skill.get("description") or name),
        )

    def record_outcome(
        self,
        name: str,
        *,
        success: bool,
        note: str = "",
        session_id: str = "",
        task_fingerprint: str = "",
    ) -> None:
        """Store skill execution outcomes in shared memory when available."""
        if self._gateway is None or not getattr(self._gateway, "memory", None):
            return
        try:
            self._gateway.memory.record_skill_outcome(
                skill_name=name,
                session_id=session_id,
                task_fingerprint=task_fingerprint,
                success=success,
                note=note,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, name: str) -> tuple[bool, str]:
        """Delete a user-installed skill.

        Returns ``(ok, error_message)``.  Empty error_message means success.
        """
        if self.registry is None:
            return False, "Skill registry not available."
        return self.registry.delete_skill(name)

    # ------------------------------------------------------------------
    # Read-only pass-throughs
    # ------------------------------------------------------------------

    def list_all(self) -> list[dict]:
        return self.registry.list_all() if self.registry is not None else []

    def get(self, name: str) -> "dict | None":
        return self.registry.get(name) if self.registry is not None else None

    def reload(self) -> None:
        if self.registry is not None:
            self.registry.reload()

    @property
    def skill_count(self) -> int:
        return len(self.registry) if self.registry is not None else 0
