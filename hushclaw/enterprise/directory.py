"""Organization directory for the Enterprise distro.

This is the enterprise identity substrate, not a full HR product. It models the
minimum org, unit, member, role, and team facts the kernel needs for principals,
RBAC, audit, and domain visibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Org:
    id: str
    name: str
    slug: str = "default"
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "slug": self.slug, "status": self.status}


@dataclass(frozen=True, slots=True)
class OrgUnit:
    id: str
    name: str
    parent_id: str = ""
    kind: str = "department"
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "kind": self.kind,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Member:
    id: str
    display_name: str
    email: str = ""
    unit_id: str = ""
    title: str = ""
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "email": self.email,
            "unit_id": self.unit_id,
            "title": self.title,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Role:
    id: str
    name: str
    permissions: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    member_id: str
    role_id: str
    scope: str = "org"
    scope_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role_id": self.role_id,
            "scope": self.scope,
            "scope_id": self.scope_id,
        }


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    name: str
    member_ids: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "member_ids": list(self.member_ids),
        }


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    org: Org
    units: tuple[OrgUnit, ...] = ()
    members: tuple[Member, ...] = ()
    roles: tuple[Role, ...] = ()
    assignments: tuple[RoleAssignment, ...] = ()
    teams: tuple[Team, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org.to_dict(),
            "units": [item.to_dict() for item in self.units],
            "members": [item.to_dict() for item in self.members],
            "roles": [item.to_dict() for item in self.roles],
            "assignments": [item.to_dict() for item in self.assignments],
            "teams": [item.to_dict() for item in self.teams],
        }


class EnterpriseDirectory:
    """Read-only v1 directory bootstrap for Enterprise distro."""

    def __init__(self, snapshot: DirectorySnapshot | None = None) -> None:
        self._snapshot = snapshot or self.default_snapshot()

    @staticmethod
    def default_snapshot() -> DirectorySnapshot:
        org = Org(id="org-default", name="Default Organization")
        units = (
            OrgUnit(id="unit-executive", name="Executive"),
            OrgUnit(id="unit-revenue", name="Revenue"),
            OrgUnit(id="unit-operations", name="Operations"),
        )
        members = (
            Member(
                id="local-user",
                display_name="Local Admin",
                email="local@hushclaw.enterprise",
                unit_id="unit-executive",
                title="Enterprise Admin",
            ),
        )
        roles = (
            Role(
                id="owner",
                name="Owner",
                description="Full enterprise workspace administration.",
                permissions=("enterprise.admin", "domain.manage", "audit.read"),
            ),
            Role(
                id="member",
                name="Member",
                description="Default enterprise workspace user.",
                permissions=("domain.use", "memory.read"),
            ),
            Role(
                id="domain-admin",
                name="Domain Admin",
                description="Can configure assigned business domains.",
                permissions=("domain.configure", "domain.use"),
            ),
        )
        assignments = (
            RoleAssignment(member_id="local-user", role_id="owner", scope="org", scope_id=org.id),
        )
        teams = (
            Team(id="team-core", name="Core Team", member_ids=("local-user",), description="Bootstrap enterprise administrators."),
        )
        return DirectorySnapshot(
            org=org,
            units=units,
            members=members,
            roles=roles,
            assignments=assignments,
            teams=teams,
        )

    def snapshot(self) -> DirectorySnapshot:
        return self._snapshot

    def overview(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "org": snap.org.to_dict(),
            "counts": {
                "units": len(snap.units),
                "members": len(snap.members),
                "roles": len(snap.roles),
                "teams": len(snap.teams),
            },
        }

    def list_units(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.units]

    def list_members(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.members]

    def list_roles(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.roles]

    def list_role_assignments(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.assignments]

    def list_teams(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.teams]
