"""Organization directory for the Enterprise distro.

This is the enterprise identity substrate, not a full HR product. It models the
minimum org, unit, position, member, reporting, role, and team facts the
Enterprise solution needs for principals, RBAC, audit, and domain visibility.
Payroll, attendance, performance, contracts, and recruiting workflows belong in
future business domains.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any

from hushclaw.util.ids import make_id


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
class Position:
    id: str
    title: str
    unit_id: str = ""
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "unit_id": self.unit_id,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Member:
    id: str
    display_name: str
    email: str = ""
    unit_id: str = ""
    position_id: str = ""
    title: str = ""
    manager_id: str = ""
    status: str = "active"
    identity_provider: str = "local"
    identity_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "email": self.email,
            "unit_id": self.unit_id,
            "position_id": self.position_id,
            "title": self.title,
            "manager_id": self.manager_id,
            "status": self.status,
            "identity_provider": self.identity_provider,
            "identity_ref": self.identity_ref,
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
class DomainAccess:
    domain_id: str
    subject_type: str = "member"  # member | team | role
    subject_id: str = ""
    access_level: str = "use"  # use | admin

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "access_level": self.access_level,
        }


@dataclass(frozen=True, slots=True)
class AccountCredential:
    member_id: str
    login_id: str
    password_hash: str
    password_status: str = "temporary"  # temporary | active | disabled
    created: int = 0
    updated: int = 0
    last_login: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "login_id": self.login_id,
            "password_hash": self.password_hash,
            "password_status": self.password_status,
            "created": int(self.created or 0),
            "updated": int(self.updated or 0),
            "last_login": int(self.last_login or 0),
        }


@dataclass(frozen=True, slots=True)
class EnterpriseSession:
    session_id: str
    member_id: str
    created: int
    expires: int
    last_seen: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "member_id": self.member_id,
            "created": int(self.created),
            "expires": int(self.expires),
            "last_seen": int(self.last_seen or 0),
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
    positions: tuple[Position, ...] = ()
    members: tuple[Member, ...] = ()
    roles: tuple[Role, ...] = ()
    assignments: tuple[RoleAssignment, ...] = ()
    teams: tuple[Team, ...] = ()
    domain_access: tuple[DomainAccess, ...] = ()
    credentials: tuple[AccountCredential, ...] = ()
    sessions: tuple[EnterpriseSession, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org.to_dict(),
            "units": [item.to_dict() for item in self.units],
            "positions": [item.to_dict() for item in self.positions],
            "members": [item.to_dict() for item in self.members],
            "roles": [item.to_dict() for item in self.roles],
            "assignments": [item.to_dict() for item in self.assignments],
            "teams": [item.to_dict() for item in self.teams],
            "domain_access": [item.to_dict() for item in self.domain_access],
        }


class EnterpriseDirectory:
    """In-memory v1 directory foundation for Enterprise distro.

    The store is intentionally small and generic. It gives Enterprise Admin a
    durable boundary and gives AgentOS principal/RBAC code concrete org facts
    without introducing HR business workflows into the kernel.
    """

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
        positions = (
            Position(id="pos-enterprise-admin", title="Enterprise Admin", unit_id="unit-executive"),
        )
        members = (
            Member(
                id="local-user",
                display_name="Local Admin",
                email="local@hushclaw.enterprise",
                unit_id="unit-executive",
                position_id="pos-enterprise-admin",
                title="Enterprise Admin",
                identity_ref="local-user",
            ),
        )
        roles = (
            Role(
                id="owner",
                name="Owner",
                description="Full enterprise workspace administration.",
                permissions=(
                    "enterprise.admin",
                    "directory.manage",
                    "role.manage",
                    "module.manage",
                    "domain.manage",
                    "audit.read",
                ),
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
        now = int(time.time())
        credentials = (
            AccountCredential(
                member_id="local-user",
                login_id="local@hushclaw.enterprise",
                password_hash=_hash_password("hushclaw-admin"),
                password_status="temporary",
                created=now,
                updated=now,
            ),
        )
        teams = (
            Team(
                id="team-core",
                name="Core Team",
                member_ids=("local-user",),
                description="Bootstrap enterprise administrators.",
            ),
        )
        return DirectorySnapshot(
            org=org,
            units=units,
            positions=positions,
            members=members,
            roles=roles,
            assignments=assignments,
            teams=teams,
            credentials=credentials,
        )

    def snapshot(self) -> DirectorySnapshot:
        return self._snapshot

    def overview(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "org": snap.org.to_dict(),
            "counts": {
                "units": len(snap.units),
                "positions": len(snap.positions),
                "members": len(snap.members),
                "roles": len(snap.roles),
                "teams": len(snap.teams),
                "domain_access": len(snap.domain_access),
            },
        }

    def list_units(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.units]

    def list_positions(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.positions]

    def list_members(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.members]

    def list_roles(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.roles]

    def list_role_assignments(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.assignments]

    def list_teams(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._snapshot.teams]

    def list_domain_access(self, domain_id: str = "") -> list[dict[str, Any]]:
        items = self._snapshot.domain_access
        if domain_id:
            items = tuple(item for item in items if item.domain_id == str(domain_id))
        return [item.to_dict() for item in items]

    def list_credentials(self) -> list[dict[str, Any]]:
        members_by_id = {item.id: item for item in self._snapshot.members}
        result: list[dict[str, Any]] = []
        for credential in self._snapshot.credentials:
            member = members_by_id.get(credential.member_id)
            result.append({
                "member_id": credential.member_id,
                "login_id": credential.login_id,
                "password_status": credential.password_status,
                "created": credential.created,
                "updated": credential.updated,
                "last_login": credential.last_login,
                "member_name": member.display_name if member else "",
            })
        return result

    def upsert_unit(self, data: dict[str, Any]) -> dict[str, Any]:
        unit = OrgUnit(
            id=str(data.get("id") or make_id("unit-")),
            name=str(data.get("name") or "Untitled Unit").strip() or "Untitled Unit",
            parent_id=str(data.get("parent_id") or ""),
            kind=str(data.get("kind") or "department"),
            status=str(data.get("status") or "active"),
        )
        self._snapshot = replace(
            self._snapshot,
            units=_upsert(self._snapshot.units, unit, key=lambda item: item.id),
        )
        return unit.to_dict()

    def upsert_position(self, data: dict[str, Any]) -> dict[str, Any]:
        position = Position(
            id=str(data.get("id") or make_id("pos-")),
            title=str(data.get("title") or "Untitled Position").strip() or "Untitled Position",
            unit_id=str(data.get("unit_id") or ""),
            status=str(data.get("status") or "active"),
        )
        self._snapshot = replace(
            self._snapshot,
            positions=_upsert(self._snapshot.positions, position, key=lambda item: item.id),
        )
        return position.to_dict()

    def upsert_member(self, data: dict[str, Any]) -> dict[str, Any]:
        member = Member(
            id=str(data.get("id") or make_id("mem-")),
            display_name=str(data.get("display_name") or data.get("name") or "Untitled Member").strip()
            or "Untitled Member",
            email=str(data.get("email") or ""),
            unit_id=str(data.get("unit_id") or ""),
            position_id=str(data.get("position_id") or ""),
            title=str(data.get("title") or ""),
            manager_id=str(data.get("manager_id") or ""),
            status=str(data.get("status") or "active"),
            identity_provider=str(data.get("identity_provider") or "local"),
            identity_ref=str(data.get("identity_ref") or data.get("email") or ""),
        )
        self._snapshot = replace(
            self._snapshot,
            members=_upsert(self._snapshot.members, member, key=lambda item: item.id),
        )
        temporary_password = str(data.get("temporary_password") or "").strip()
        if temporary_password:
            self.set_member_password(member.id, temporary_password, temporary=True)
        return member.to_dict()

    def set_member_password(self, member_id: str, password: str, *, temporary: bool = True) -> dict[str, Any]:
        member_id = str(member_id or "").strip()
        password = str(password or "")
        if not member_id:
            return {"ok": False, "member_id": member_id, "error": "member_id required"}
        if len(password) < 8:
            return {"ok": False, "member_id": member_id, "error": "password must be at least 8 characters"}
        member = self.member(member_id)
        if member is None:
            return {"ok": False, "member_id": member_id, "error": "member not found"}
        login_id = (member.email or member.identity_ref or member.id).strip().lower()
        now = int(time.time())
        credential = AccountCredential(
            member_id=member.id,
            login_id=login_id,
            password_hash=_hash_password(password),
            password_status="temporary" if temporary else "active",
            created=now,
            updated=now,
        )
        existing = self.credential_for_member(member.id)
        if existing is not None:
            credential = replace(credential, created=existing.created or now, last_login=existing.last_login)
        self._snapshot = replace(
            self._snapshot,
            credentials=_upsert(self._snapshot.credentials, credential, key=lambda item: item.member_id),
        )
        return {
            "ok": True,
            "member_id": member.id,
            "login_id": login_id,
            "password_status": credential.password_status,
        }

    def upsert_role(self, data: dict[str, Any]) -> dict[str, Any]:
        permissions = data.get("permissions") or ()
        role = Role(
            id=str(data.get("id") or make_id("role-")),
            name=str(data.get("name") or "Untitled Role").strip() or "Untitled Role",
            description=str(data.get("description") or ""),
            permissions=tuple(str(item) for item in permissions if str(item).strip()),
        )
        self._snapshot = replace(
            self._snapshot,
            roles=_upsert(self._snapshot.roles, role, key=lambda item: item.id),
        )
        return role.to_dict()

    def assign_role(
        self,
        member_id: str,
        role_id: str,
        *,
        scope: str = "org",
        scope_id: str = "",
    ) -> dict[str, Any]:
        assignment = RoleAssignment(
            member_id=str(member_id),
            role_id=str(role_id),
            scope=str(scope or "org"),
            scope_id=str(scope_id or self._snapshot.org.id),
        )
        assignments = tuple(
            item
            for item in self._snapshot.assignments
            if not (
                item.member_id == assignment.member_id
                and item.role_id == assignment.role_id
                and item.scope == assignment.scope
                and item.scope_id == assignment.scope_id
            )
        ) + (assignment,)
        self._snapshot = replace(self._snapshot, assignments=assignments)
        return assignment.to_dict()

    def revoke_role(
        self,
        member_id: str,
        role_id: str,
        *,
        scope: str = "org",
        scope_id: str = "",
    ) -> bool:
        target_scope_id = str(scope_id or self._snapshot.org.id)
        before = len(self._snapshot.assignments)
        assignments = tuple(
            item
            for item in self._snapshot.assignments
            if not (
                item.member_id == str(member_id)
                and item.role_id == str(role_id)
                and item.scope == str(scope or "org")
                and item.scope_id == target_scope_id
            )
        )
        self._snapshot = replace(self._snapshot, assignments=assignments)
        return len(assignments) != before

    def upsert_team(self, data: dict[str, Any]) -> dict[str, Any]:
        members = data.get("member_ids") or ()
        team = Team(
            id=str(data.get("id") or make_id("team-")),
            name=str(data.get("name") or "Untitled Team").strip() or "Untitled Team",
            description=str(data.get("description") or ""),
            member_ids=tuple(str(item) for item in members if str(item).strip()),
        )
        self._snapshot = replace(
            self._snapshot,
            teams=_upsert(self._snapshot.teams, team, key=lambda item: item.id),
        )
        return team.to_dict()

    def grant_domain_access(
        self,
        domain_id: str,
        subject_type: str,
        subject_id: str,
        *,
        access_level: str = "use",
    ) -> dict[str, Any]:
        access = DomainAccess(
            domain_id=str(domain_id),
            subject_type=str(subject_type or "member"),
            subject_id=str(subject_id),
            access_level=str(access_level or "use"),
        )
        access_items = tuple(
            item
            for item in self._snapshot.domain_access
            if not (
                item.domain_id == access.domain_id
                and item.subject_type == access.subject_type
                and item.subject_id == access.subject_id
            )
        ) + (access,)
        self._snapshot = replace(self._snapshot, domain_access=access_items)
        return access.to_dict()

    def revoke_domain_access(self, domain_id: str, subject_type: str, subject_id: str) -> bool:
        before = len(self._snapshot.domain_access)
        access_items = tuple(
            item
            for item in self._snapshot.domain_access
            if not (
                item.domain_id == str(domain_id)
                and item.subject_type == str(subject_type or "member")
                and item.subject_id == str(subject_id)
            )
        )
        self._snapshot = replace(self._snapshot, domain_access=access_items)
        return len(access_items) != before

    def member_role_ids(self, member_id: str) -> set[str]:
        return {
            item.role_id
            for item in self._snapshot.assignments
            if item.member_id == str(member_id)
        }

    def member(self, member_id: str) -> Member | None:
        for item in self._snapshot.members:
            if item.id == str(member_id):
                return item
        return None

    def active_member(self, member_id: str) -> Member | None:
        member = self.member(member_id)
        if member is None or member.status != "active":
            return None
        return member

    def credential_for_member(self, member_id: str) -> AccountCredential | None:
        for item in self._snapshot.credentials:
            if item.member_id == str(member_id):
                return item
        return None

    def credential_for_login(self, login_id: str) -> AccountCredential | None:
        normalized = str(login_id or "").strip().lower()
        for item in self._snapshot.credentials:
            if item.login_id.strip().lower() == normalized:
                return item
        return None

    def roles_for_member(self, member_id: str) -> tuple[str, ...]:
        role_ids = sorted(self.member_role_ids(member_id) | {"member"})
        return tuple(role_ids)

    def authenticate(self, login_id: str, password: str, *, ttl_seconds: int = 60 * 60 * 12) -> dict[str, Any]:
        credential = self.credential_for_login(login_id)
        if credential is None or credential.password_status == "disabled":
            return {"ok": False, "error": "invalid credentials"}
        member = self.active_member(credential.member_id)
        if member is None:
            return {"ok": False, "error": "member inactive or missing"}
        if not _verify_password(str(password or ""), credential.password_hash):
            return {"ok": False, "error": "invalid credentials"}

        now = int(time.time())
        session = EnterpriseSession(
            session_id=secrets.token_urlsafe(32),
            member_id=member.id,
            created=now,
            expires=now + max(300, int(ttl_seconds)),
            last_seen=now,
        )
        updated_credential = replace(credential, last_login=now)
        self._snapshot = replace(
            self._snapshot,
            credentials=_upsert(self._snapshot.credentials, updated_credential, key=lambda item: item.member_id),
            sessions=_upsert(self._prune_sessions(self._snapshot.sessions, now), session, key=lambda item: item.session_id),
        )
        return {
            "ok": True,
            "session": session.to_dict(),
            "member": member.to_dict(),
            "roles": list(self.roles_for_member(member.id)),
            "password_status": credential.password_status,
        }

    def session(self, session_id: str) -> EnterpriseSession | None:
        now = int(time.time())
        target = str(session_id or "")
        sessions: list[EnterpriseSession] = []
        found: EnterpriseSession | None = None
        for item in self._snapshot.sessions:
            if item.expires <= now:
                continue
            if item.session_id == target:
                item = replace(item, last_seen=now)
                found = item
            sessions.append(item)
        if len(sessions) != len(self._snapshot.sessions) or found is not None:
            self._snapshot = replace(self._snapshot, sessions=tuple(sessions))
        return found

    def member_for_session(self, session_id: str) -> tuple[Member, tuple[str, ...], EnterpriseSession] | None:
        session = self.session(session_id)
        if session is None:
            return None
        member = self.active_member(session.member_id)
        if member is None:
            return None
        return member, self.roles_for_member(member.id), session

    def logout(self, session_id: str) -> bool:
        before = len(self._snapshot.sessions)
        sessions = tuple(item for item in self._snapshot.sessions if item.session_id != str(session_id or ""))
        self._snapshot = replace(self._snapshot, sessions=sessions)
        return len(sessions) != before

    @staticmethod
    def _prune_sessions(sessions: tuple[EnterpriseSession, ...], now: int) -> tuple[EnterpriseSession, ...]:
        return tuple(item for item in sessions if item.expires > now)

    def member_team_ids(self, member_id: str) -> set[str]:
        return {
            item.id
            for item in self._snapshot.teams
            if str(member_id) in item.member_ids
        }

    def domain_access_level(self, member_id: str, domain_id: str, roles: tuple[str, ...] = ()) -> str:
        if "owner" in roles:
            return "admin"
        role_ids = self.member_role_ids(member_id) | {str(item) for item in roles}
        team_ids = self.member_team_ids(member_id)
        levels: list[str] = []
        for item in self._snapshot.domain_access:
            if item.domain_id != str(domain_id):
                continue
            if item.subject_type == "member" and item.subject_id == str(member_id):
                levels.append(item.access_level)
            elif item.subject_type == "team" and item.subject_id in team_ids:
                levels.append(item.access_level)
            elif item.subject_type == "role" and item.subject_id in role_ids:
                levels.append(item.access_level)
        if "admin" in levels:
            return "admin"
        if "use" in levels:
            return "use"
        return ""

    def can_use_domain(self, member_id: str, domain_id: str, roles: tuple[str, ...] = ()) -> bool:
        return self.domain_access_level(member_id, domain_id, roles) in {"use", "admin"}

    def can_admin_domain(self, member_id: str, domain_id: str, roles: tuple[str, ...] = ()) -> bool:
        return self.domain_access_level(member_id, domain_id, roles) == "admin"

    def deactivate_member(self, member_id: str) -> bool:
        for member in self._snapshot.members:
            if member.id == member_id:
                updated = replace(member, status="inactive")
                self._snapshot = replace(
                    self._snapshot,
                    members=_upsert(self._snapshot.members, updated, key=lambda item: item.id),
                )
                return True
        return False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnterpriseDirectory":
        org_data = data.get("org") or {}
        org = Org(
            id=str(org_data.get("id") or "org-default"),
            name=str(org_data.get("name") or "Default Organization"),
            slug=str(org_data.get("slug") or "default"),
            status=str(org_data.get("status") or "active"),
        )
        credentials = tuple(AccountCredential(**item) for item in data.get("credentials", []))
        if not credentials and any(str(item.get("id") or "") == "local-user" for item in data.get("members", [])):
            now = int(time.time())
            credentials = (
                AccountCredential(
                    member_id="local-user",
                    login_id="local@hushclaw.enterprise",
                    password_hash=_hash_password("hushclaw-admin"),
                    password_status="temporary",
                    created=now,
                    updated=now,
                ),
            )
        snapshot = DirectorySnapshot(
            org=org,
            units=tuple(OrgUnit(**item) for item in data.get("units", [])),
            positions=tuple(Position(**item) for item in data.get("positions", [])),
            members=tuple(Member(**item) for item in data.get("members", [])),
            roles=tuple(
                Role(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    permissions=tuple(item.get("permissions") or ()),
                )
                for item in data.get("roles", [])
            ),
            assignments=tuple(RoleAssignment(**item) for item in data.get("assignments", [])),
            teams=tuple(
                Team(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    member_ids=tuple(item.get("member_ids") or ()),
                )
                for item in data.get("teams", [])
            ),
            domain_access=tuple(DomainAccess(**item) for item in data.get("domain_access", [])),
            credentials=credentials,
            sessions=tuple(EnterpriseSession(**item) for item in data.get("sessions", [])),
        )
        return cls(snapshot)


class EnterpriseDirectoryStore:
    """SQLite persistence for EnterpriseDirectory snapshots."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def load(self) -> EnterpriseDirectory:
        rows = self.conn.execute(
            "SELECT object_type, payload_json FROM enterprise_directory"
        ).fetchall()
        if not rows:
            directory = EnterpriseDirectory()
            self.save(directory)
            return directory
        grouped: dict[str, Any] = {
            "org": {},
            "units": [],
            "positions": [],
            "members": [],
            "roles": [],
            "assignments": [],
            "teams": [],
            "domain_access": [],
            "credentials": [],
            "sessions": [],
        }
        plural = {
            "org_unit": "units",
            "position": "positions",
            "member": "members",
            "role": "roles",
            "role_assignment": "assignments",
            "team": "teams",
            "domain_access": "domain_access",
            "account_credential": "credentials",
            "enterprise_session": "sessions",
        }
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            object_type = str(row["object_type"])
            if object_type == "org":
                grouped["org"] = payload
            elif object_type in plural:
                grouped[plural[object_type]].append(payload)
        if not grouped["org"]:
            grouped["org"] = EnterpriseDirectory.default_snapshot().org.to_dict()
        return EnterpriseDirectory.from_dict(grouped)

    def save(self, directory: EnterpriseDirectory) -> None:
        snap = directory.snapshot()
        now = int(time.time() * 1000)
        self._put("org", snap.org.id, snap.org.to_dict(), now)
        for object_type, items in (
            ("org_unit", snap.units),
            ("position", snap.positions),
            ("member", snap.members),
            ("role", snap.roles),
            ("role_assignment", snap.assignments),
            ("team", snap.teams),
            ("domain_access", snap.domain_access),
            ("account_credential", snap.credentials),
            ("enterprise_session", snap.sessions),
        ):
            for item in items:
                item_id = _directory_object_id(object_type, item)
                self._put(object_type, item_id, item.to_dict(), now)
        self.conn.commit()

    def _put(self, object_type: str, object_id: str, payload: dict[str, Any], updated: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO enterprise_directory "
            "(object_type, object_id, payload_json, updated) VALUES (?, ?, ?, ?)",
            (object_type, object_id, json.dumps(payload, ensure_ascii=False), updated),
        )


def _directory_object_id(object_type: str, item: Any) -> str:
    if object_type == "role_assignment":
        return f"{item.member_id}:{item.role_id}:{item.scope}:{item.scope_id}"
    if object_type == "domain_access":
        return f"{item.domain_id}:{item.subject_type}:{item.subject_id}"
    if object_type == "account_credential":
        return str(item.member_id)
    if object_type == "enterprise_session":
        return str(item.session_id)
    return str(item.id)


def _upsert(items: tuple[Any, ...], item: Any, *, key) -> tuple[Any, ...]:
    item_key = key(item)
    replaced = False
    result = []
    for current in items:
        if key(current) == item_key:
            result.append(item)
            replaced = True
        else:
            result.append(current)
    if not replaced:
        result.append(item)
    return tuple(result)


def _hash_password(password: str, *, iterations: int = 260_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, digest_raw = str(encoded or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
