"""MemoryPort boundary for Agent OS storage adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.runtime.principal import RuntimePrincipal


@dataclass(slots=True)
class MemoryRecord:
    content: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = "global"
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPort(ABC):
    @abstractmethod
    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        principal: RuntimePrincipal | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def recall(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> str: ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> list[dict]: ...

    @abstractmethod
    def update(
        self,
        note_id: str,
        content: str,
        *,
        principal: RuntimePrincipal | None = None,
        tags: list[str] | None = None,
    ) -> bool: ...

    @abstractmethod
    def delete(self, note_id: str, *, principal: RuntimePrincipal | None = None) -> bool: ...

    @abstractmethod
    def promote(
        self,
        note_id: str,
        target_scope: str,
        *,
        principal: RuntimePrincipal | None = None,
    ) -> bool: ...


class SQLiteMemoryPort(MemoryPort):
    """Adapter around the existing MemoryStore facade."""

    def __init__(self, store) -> None:
        self.store = store

    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        principal: RuntimePrincipal | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        from hushclaw.runtime.principal import current_principal  # lazy — avoids circular import through memory.__init__
        principal = principal or current_principal()
        metadata = metadata or {}
        tags = list(metadata.get("tags") or [])
        title = str(metadata.get("title") or "")
        note_type = str(metadata.get("note_type") or "fact")
        memory_kind = str(metadata.get("memory_kind") or "")
        return self.store.remember(
            content,
            title=title,
            tags=tags,
            scope=scope,
            note_type=note_type,
            memory_kind=memory_kind,
        )

    def recall(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> str:
        if hasattr(self.store, "recall_with_budget"):
            text = self.store.recall_with_budget(query, limit=limit, scopes=scopes)
            if text:
                return text
            if scopes and hasattr(self.store, "list_recent_notes_by_scopes"):
                rows = self.store.list_recent_notes_by_scopes(scopes, limit=limit)
                if rows:
                    return "\n\n".join(
                        f"[{i}] {r.get('title') or r.get('note_id')}\n{str(r.get('body') or '')[:300]}"
                        for i, r in enumerate(rows, 1)
                    )
            return text
        return self.store.recall(query, limit=limit)

    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> list[dict]:
        if scopes and hasattr(self.store, "recall_with_budget"):
            # Existing search() has no scope filter; use recent scoped notes as a conservative fallback.
            return self.store.list_recent_notes_by_scopes(scopes, limit=limit)
        return self.store.search(query, limit=limit)

    def update(
        self,
        note_id: str,
        content: str,
        *,
        principal: RuntimePrincipal | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        return self.store.update_note(note_id, content, tags)

    def delete(self, note_id: str, *, principal: RuntimePrincipal | None = None) -> bool:
        return self.store.delete_note(note_id)

    def promote(
        self,
        note_id: str,
        target_scope: str,
        *,
        principal: RuntimePrincipal | None = None,
    ) -> bool:
        note = self.store.get_note(note_id)
        if not note:
            return False
        content = str(note.get("body") or note.get("content") or "")
        title = str(note.get("title") or "")
        tags = list(note.get("tags") or [])
        self.store.remember(content, title=title, tags=tags, scope=target_scope)
        return True
