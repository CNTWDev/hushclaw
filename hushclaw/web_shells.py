"""Web shell routing for product solutions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


_PACKAGE_ROOT = Path(__file__).parent
_WEB_DIR = _PACKAGE_ROOT / "web"
_OPC_WEB_DIR = _PACKAGE_ROOT / "solutions" / "opc" / "web"

@dataclass(frozen=True, slots=True)
class WebShell:
    id: str
    name: str
    base_path: str
    asset_dir: Path
    entrypoint: str = "index.html"
    kind: str = "personal"
    requires_distro: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_path": self.base_path,
            "entrypoint": self.entrypoint,
            "kind": self.kind,
            "requires_distro": list(self.requires_distro),
        }


class WebShellRegistry:
    """Resolve request paths to product shell assets."""

    def __init__(self, distro: Any = None) -> None:
        self.distro = distro
        self._shells = {
            "personal": WebShell(
                id="personal",
                name="HushClaw Personal",
                base_path="/personal",
                asset_dir=_WEB_DIR,
                kind="personal",
                requires_distro=("personal",),
            ),
            "opc": WebShell(
                id="opc",
                name="OPC",
                base_path="/opc",
                asset_dir=_OPC_WEB_DIR,
                kind="solution",
                requires_distro=("personal",),
            ),
        }

    def distro_id(self) -> str:
        try:
            return str(self.distro.manifest().id)
        except Exception:
            return "personal"

    def default_shell_id(self) -> str:
        try:
            manifest = self.distro.manifest()
            return str(getattr(manifest, "web_shell", "") or "personal")
        except Exception:
            return "personal"

    def default_path(self) -> str:
        shell = self._shells.get(self.default_shell_id()) or self._shells["personal"]
        return shell.base_path

    def list_available(self) -> list[dict[str, Any]]:
        distro_id = self.distro_id()
        out = []
        for shell in self._shells.values():
            if not shell.requires_distro or distro_id in shell.requires_distro:
                out.append(shell.to_dict())
        return out

    def resolve(self, path: str) -> tuple[WebShell, Path] | None:
        distro_id = self.distro_id()
        for shell in sorted(self._shells.values(), key=lambda item: len(item.base_path), reverse=True):
            if shell.requires_distro and distro_id not in shell.requires_distro:
                continue
            if path == shell.base_path or path.startswith(shell.base_path + "/"):
                rel = path[len(shell.base_path):].lstrip("/")
                candidate = (shell.asset_dir / (rel or shell.entrypoint)).resolve()
                root = shell.asset_dir.resolve()
                try:
                    candidate.relative_to(root)
                except Exception:
                    return None
                if candidate.is_dir():
                    candidate = (candidate / shell.entrypoint).resolve()
                    try:
                        candidate.relative_to(root)
                    except Exception:
                        return None
                return shell, candidate
        return None
