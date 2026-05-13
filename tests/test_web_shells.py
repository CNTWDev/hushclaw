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


def test_web_shell_registry_routes_enterprise_workspace_and_admin():
    registry = WebShellRegistry(_distro("enterprise", "enterprise_workspace", "enterprise_admin"))

    assert registry.default_path() == "/enterprise"
    shell_ids = {item["id"] for item in registry.list_available()}
    assert shell_ids == {"enterprise_workspace", "enterprise_admin"}

    workspace = registry.resolve("/enterprise")
    assert workspace is not None
    assert workspace[0].id == "enterprise_workspace"
    assert workspace[1].name == "index.html"
    assert workspace[1].exists()

    admin = registry.resolve("/enterprise/admin")
    assert admin is not None
    assert admin[0].id == "enterprise_admin"
    assert admin[1].name == "index.html"
    assert admin[1].exists()

    admin_js = registry.resolve("/enterprise/admin/admin-shell.js")
    assert admin_js is not None
    assert admin_js[1].name == "admin-shell.js"


def test_web_shell_registry_hides_enterprise_shells_from_personal_distro():
    registry = WebShellRegistry(_distro("personal", "personal"))

    assert registry.resolve("/enterprise") is None
    assert {item["id"] for item in registry.list_available()} == {"personal"}
