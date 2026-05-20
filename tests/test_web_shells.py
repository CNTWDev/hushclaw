from __future__ import annotations

from hushclaw.distro.base import DistroManifest
from hushclaw.web_shells import WebShellRegistry


class _Distro:
    def __init__(self, manifest: DistroManifest) -> None:
        self._manifest = manifest

    def manifest(self):
        return self._manifest


def _distro(distro_id: str, web_shell: str, admin_shell: str = ""):
    return _Distro(DistroManifest(
        id=distro_id,
        name=distro_id,
        description="test",
        storage_profile="local_sqlite",
        policy_profile="test",
        web_shell=web_shell,
        admin_shell=admin_shell,
    ))


def test_web_shell_registry_routes_personal_to_existing_web_assets():
    registry = WebShellRegistry(_distro("personal", "personal"))

    assert registry.default_path() == "/personal"
    resolved = registry.resolve("/personal")
    assert resolved is not None
    shell, path = resolved
    assert shell.id == "personal"
    assert path.name == "index.html"
    assert path.exists()


def test_web_shell_registry_has_no_enterprise_portal_routes():
    registry = WebShellRegistry(_distro("personal", "personal"))

    assert registry.resolve("/enterprise") is None
    assert registry.resolve("/enterprise/admin") is None
    assert {item["id"] for item in registry.list_available()} == {"personal", "opc"}


def test_web_shell_registry_routes_opc_to_independent_solution_assets():
    registry = WebShellRegistry(_distro("personal", "personal"))

    resolved = registry.resolve("/opc")
    assert resolved is not None
    shell, path = resolved
    assert shell.id == "opc"
    assert shell.kind == "solution"
    assert path.name == "index.html"
    assert path.exists()
    assert registry.resolve("/opc/opc-shell.js")[1].name == "opc-shell.js"
