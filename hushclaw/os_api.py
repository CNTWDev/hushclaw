"""Agent OS service facade.

Product shells should move toward this boundary instead of importing kernel
objects directly. The facade is intentionally thin for v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hushclaw.extensions import ExtensionRegistry
from hushclaw.memory.kinds import SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
from hushclaw.memory.ports import SQLiteMemoryPort
from hushclaw.runtime.audit import AuditEvent
from hushclaw.runtime.principal import RuntimePrincipal, current_principal
from hushclaw.tools.base import to_api_schema


@dataclass(slots=True)
class AgentOSService:
    gateway: Any
    distro: Any = None  # DistroAdapter | None — injected by DistroRuntime.assemble()

    @property
    def principal(self) -> RuntimePrincipal:
        return current_principal()

    def distro_manifest(self) -> dict:
        if self.distro is not None:
            return self.distro.manifest().to_dict()
        return {}

    def list_agents(self) -> list[dict]:
        return self.gateway.list_agents()

    def list_tools(self) -> list[dict]:
        registry = self.gateway.base_agent.registry
        return [to_api_schema(td) for td in registry.list_tools()]

    def list_extensions(self) -> list[dict]:
        result = ExtensionRegistry(self.gateway).list()
        distro_id = self.distro.manifest().id if self.distro is not None else "personal"
        return [{"distro_id": distro_id, **ext} for ext in result]

    def memory_port(self) -> SQLiteMemoryPort:
        return SQLiteMemoryPort(self.gateway.memory)

    def search_memory(self, query: str, *, scopes: list[str] | None = None, limit: int = 5) -> list[dict]:
        return self.memory_port().search(query, scopes=scopes, principal=self.principal, limit=limit)

    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.memory_port().remember(content, scope=scope, principal=self.principal, metadata=metadata)

    def audit_events(self, *, session_id: str = "", limit: int = 200) -> list[dict]:
        mem = self.gateway.memory
        if session_id:
            events = mem.events.session_events(session_id, limit=limit)
            return [e for e in events if str(e.get("type", "")).startswith("audit:")]
        return mem.events.type_prefix_events("audit:", limit=limit)

    def build_audit_event(self, event_type: str, **kwargs: Any) -> AuditEvent:
        return AuditEvent(event_type=event_type, principal=self.principal, **kwargs)

    # Session APIs

    def list_sessions(
        self,
        *,
        limit: int,
        offset: int = 0,
        include_scheduled: bool = True,
        max_idle_days: int = 0,
        workspace: str | None = None,
    ) -> tuple[list[dict], bool]:
        fetch_limit = max(1, int(limit)) + 1
        items = self.gateway.memory.list_sessions(
            limit=fetch_limit,
            include_scheduled=include_scheduled,
            max_idle_days=max(0, int(max_idle_days)),
            workspace=workspace,
            offset=max(0, int(offset)),
        )
        has_more = len(items) > int(limit)
        return (items[: int(limit)] if has_more else items), has_more

    def session_history(self, session_id: str) -> dict:
        mem = self.gateway.memory
        return {
            "turns": mem.load_session_history(session_id),
            "summary": mem.load_session_summary(session_id) if session_id else None,
            "lineage": mem.get_session_lineage(session_id) if session_id else [],
        }

    def search_sessions(
        self,
        *,
        query: str,
        limit: int = 20,
        include_scheduled: bool = True,
        workspace: str | None = None,
    ) -> list[dict]:
        return self.gateway.memory.search_sessions(
            query=query,
            limit=max(1, int(limit)),
            include_scheduled=include_scheduled,
            workspace=workspace,
        )

    def session_lineage(self, session_id: str) -> list[dict]:
        return self.gateway.memory.get_session_lineage(session_id) if session_id else []

    def delete_session(self, session_id: str) -> bool:
        return self.gateway.memory.delete_session(session_id) if session_id else False

    def set_message_state(self, message_id: str, *, session_id: str, action: str) -> dict:
        kwargs: dict[str, Any]
        if action == "hide":
            kwargs = {"hidden": True}
        elif action == "exclude":
            kwargs = {"excluded": True}
        else:
            return {"ok": False, "error": "Unknown message state action", "message_id": message_id}
        ok = self.gateway.memory.set_message_state(message_id, session_id=session_id, **kwargs) if message_id else False
        if ok:
            self.gateway.clear_all_cached_loops()
        return {
            "ok": ok,
            "message_id": self.gateway.memory.canonical_message_id(message_id) if message_id else "",
            "session_id": session_id,
            "action": action,
        }

    # Memory APIs

    @staticmethod
    def normalize_note_payload(item: dict) -> dict:
        out = dict(item or {})
        created = out.get("created")
        modified = out.get("modified")
        out["created_at"] = int(created or modified or 0) if (created or modified) else 0
        if modified is not None:
            out["updated_at"] = int(modified)
        return out

    @staticmethod
    def _is_auto_extract_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_extract" in tags

    @staticmethod
    def _is_system_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if bool(SYSTEM_MEMORY_TAGS & set(tags)):
            return True
        return item.get("memory_kind") in {"telemetry", "session_memory"}

    def list_memories(
        self,
        *,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        include_auto: bool = False,
        memory_kinds: set[str] | None = None,
        workspace: str = "",
    ) -> tuple[list[dict], bool]:
        agent = self.gateway.base_agent
        exclude_tags = sorted(SYSTEM_MEMORY_TAGS)
        if not include_auto:
            exclude_tags.append("_auto_extract")
        fetch_limit = max(1, int(limit)) + 1
        if query:
            items = agent.search(query, limit=fetch_limit, include_kinds=memory_kinds)
            items = [m for m in items if not self._is_system_note(m)]
            if not include_auto:
                items = [m for m in items if not self._is_auto_extract_note(m)]
        elif workspace:
            items = agent.memory.list_recent_notes_by_scopes(
                scopes=["global", f"workspace:{workspace}"],
                limit=fetch_limit,
                offset=max(0, int(offset)),
                exclude_tags=exclude_tags,
                include_kinds=memory_kinds,
            )
        else:
            items = agent.list_memories(
                limit=fetch_limit,
                offset=max(0, int(offset)),
                exclude_tags=exclude_tags,
                include_kinds=memory_kinds,
            )
        has_more = len(items) > int(limit)
        if has_more:
            items = items[: int(limit)]
        return [self.normalize_note_payload(m) for m in items], has_more

    def delete_memory(self, note_id: str) -> bool:
        return self.gateway.base_agent.forget(note_id) if note_id else False

    async def promote_to_hub(
        self,
        note_id: str,
        *,
        scope: str = "",
        hub_url: str = "",
        token: str = "",
    ) -> dict:
        """Promote a local note to the team Knowledge Hub.

        The Hub is a separate deploy unit — this is the user's explicit act of
        sharing. The local note is NOT deleted; it stays in personal memory.

        Args:
            note_id: Local note to promote.
            scope: Target hub scope, e.g. "team:backend". Uses hub default if empty.
            hub_url: Override the Hub URL from config.
            token: Override the Hub token from config.

        Returns:
            {"ok": True, "hub_note_id": str} on success,
            {"ok": False, "error": str} on failure.
        """
        import asyncio
        mem = self.gateway.memory
        note = mem.get_note(note_id) if hasattr(mem, "get_note") else None
        if note is None:
            return {"ok": False, "error": f"Note {note_id!r} not found in local memory"}

        # Resolve hub connector: try gateway-attached connector first, then ephemeral one.
        hub = getattr(self.gateway, "_knowledge_hub", None)
        if hub is None and hub_url:
            from hushclaw.connectors.knowledge import KnowledgeConnector
            from hushclaw.config.schema import KnowledgeHubConfig
            cfg = KnowledgeHubConfig(enabled=True, url=hub_url, token=token or "")
            hub = KnowledgeConnector(cfg)
            await hub.start()

        if hub is None or not hub.connected:
            return {"ok": False, "error": "Knowledge Hub not configured or not connected"}

        from hushclaw.runtime.principal import current_principal
        principal = current_principal()
        content = str(note.get("body") or note.get("content") or "")
        hub_note_id = await hub.write_shared(
            content,
            title=str(note.get("title") or ""),
            tags=list(note.get("tags") or []),
            scope=scope,
            source_principal_id=principal.principal_id,
        )
        if hub_note_id:
            return {"ok": True, "hub_note_id": hub_note_id}
        return {"ok": False, "error": "Hub returned no note_id (promote may have failed)"}

    def learning_state(self, *, reflection_limit: int = 8, skill_outcome_limit: int = 10) -> dict:
        mem = self.gateway.memory
        return {
            "profile_snapshot": mem.user_profile.get_profile_snapshot(),
            "profile_text": mem.user_profile.render_profile_context(max_chars=1400),
            "reflections": mem.list_reflections(limit=reflection_limit),
            "skill_outcomes": mem.list_recent_skill_outcomes(limit=skill_outcome_limit),
        }

    def memory_overview(self, *, session_id: str = "", reflection_limit: int = 30) -> dict:
        mem = self.gateway.memory
        visible_kinds = USER_VISIBLE_MEMORY_KINDS
        exclude_tags = sorted(SYSTEM_MEMORY_TAGS | {"_auto_extract"})
        return {
            "profile_facts": mem.user_profile.list_facts(limit=200),
            "beliefs": mem.list_belief_models(),
            "reflections": mem.list_reflections(limit=reflection_limit),
            "recent_notes": [
                self.normalize_note_payload(n)
                for n in mem.list_recent_notes(limit=6, exclude_tags=exclude_tags, include_kinds=visible_kinds)
            ],
            "working_state": mem.load_session_working_state(session_id) if session_id else "",
        }

    def workspace_briefing_inputs(self, *, workspace: str) -> dict:
        mem = self.gateway.memory
        return {
            "sessions": mem.list_sessions(limit=6, include_scheduled=False, workspace=workspace, offset=0),
            "todos": mem.list_todos(status="pending"),
            "scheduled": mem.list_scheduled_tasks(),
            "reflections": mem.list_reflections(limit=5),
        }

    def accept_briefing_create_todo(self, todo: dict, *, fallback_title: str = "Briefing follow-up") -> dict:
        return self.gateway.memory.add_todo(
            title=str(todo.get("title") or fallback_title),
            notes=str(todo.get("notes") or "Created from proactive workspace briefing."),
            priority=int(todo.get("priority") or 0),
            tags=todo.get("tags") or ["briefing"],
        )

    def get_session_brief(self, session_id: str) -> dict | None:
        return self.gateway.memory.get_session_brief(session_id)

    def get_note(self, note_id: str) -> dict | None:
        return self.gateway.memory.get_note(note_id)

    def list_belief_models(self, scopes: list[str] | None = None) -> list[dict]:
        return self.gateway.memory.list_belief_models(scopes=scopes)

    def rebuild_belief_models(self, *, dry_run: bool = False, scopes: list[str] | None = None) -> dict:
        return self.gateway.memory.rebuild_belief_models(dry_run=dry_run, scopes=scopes)

    def list_profile_facts(self, *, limit: int = 200) -> list[dict]:
        return self.gateway.memory.user_profile.list_facts(limit=limit)

    def delete_profile_fact(self, fact_id: str) -> bool:
        return self.gateway.memory.user_profile.delete_fact(fact_id) if fact_id else False

    # Scheduled task and todo APIs

    def list_scheduled_tasks(self) -> list[dict]:
        return self.gateway.memory.list_scheduled_tasks()

    def create_scheduled_task(self, data: dict) -> dict | None:
        mem = self.gateway.memory
        task_id = mem.add_scheduled_task(
            cron=data.get("cron", ""),
            prompt=data.get("prompt", ""),
            agent=data.get("agent", ""),
            run_once=bool(data.get("run_once", False)),
            title=data.get("title", ""),
        )
        return next((t for t in mem.list_scheduled_tasks() if t["id"] == task_id), None)

    def toggle_scheduled_task(self, task_id: str, enabled: bool) -> bool:
        return self.gateway.memory.toggle_scheduled_task(task_id, enabled)

    def delete_scheduled_task(self, task_id: str) -> bool:
        return self.gateway.memory.delete_scheduled_task(task_id)

    def list_todos(self, status: str | None = None) -> list[dict]:
        return self.gateway.memory.list_todos(status=status)

    def create_todo(self, data: dict) -> dict:
        due_at = data.get("due_at")
        return self.gateway.memory.add_todo(
            title=data.get("title", ""),
            notes=data.get("notes", ""),
            priority=int(data.get("priority", 0)),
            due_at=int(due_at) if due_at else None,
            tags=data.get("tags") or [],
        )

    def update_todo(self, todo_id: str, fields: dict) -> dict | None:
        return self.gateway.memory.update_todo(todo_id, **fields)

    def delete_todo(self, todo_id: str) -> bool:
        return self.gateway.memory.delete_todo(todo_id)
