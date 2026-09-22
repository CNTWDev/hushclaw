"""MemoryPort boundary for Agent OS storage adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from hushclaw.memory.events import _conn_lock
from hushclaw.memory.fts import FTSSearch
from hushclaw.memory.kinds import RECALL_MEMORY_KINDS
from hushclaw.memory.retrieval import search_notes, visible_note_ids
from hushclaw.memory.sqlite_runtime import SQLiteReadConnections
from hushclaw.memory.vectors import VectorStore

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
        conn = getattr(store, "conn", None)
        self._lock = _conn_lock(conn) if conn is not None else None
        self._read_connections = getattr(store, "_read_connections", None)
        if self._read_connections is None and getattr(store, "data_dir", None) is not None:
            self._read_connections = SQLiteReadConnections(
                store.data_dir,
                database_encryption=getattr(store, "database_encryption", "auto"),
            )
            setattr(store, "_read_connections", self._read_connections)

    def _locked(self, fn):
        if self._lock is None:
            return fn()
        with self._lock:
            return fn()

    def _read_conn(self):
        return self._read_connections.connection() if self._read_connections is not None else None

    def _read_vector_store(self, conn) -> VectorStore:
        vec = getattr(self.store, "_vec", None)
        return VectorStore(
            conn,
            getattr(vec, "embed_provider", "local"),
            getattr(vec, "api_key", ""),
            getattr(vec, "embed_model", ""),
        )

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
        return self._locked(
            lambda: self.store.remember(
                content,
                title=title,
                tags=tags,
                scope=scope,
                note_type=note_type,
                memory_kind=memory_kind,
            )
        )

    def recall(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> str:
        conn = self._read_conn()
        if conn is None:
            return self._locked(lambda: self._format_recall(
                self.store.search(query, limit=limit, scopes=scopes,
                                  include_kinds=RECALL_MEMORY_KINDS), limit))
        results = self._read_search(query, limit=limit, scopes=scopes, include_kinds=RECALL_MEMORY_KINDS)
        if results:
            return self._format_recall(results, limit)
        return ""

    @staticmethod
    def _format_recall(results: list[dict], limit: int) -> str:
        return "\n\n".join(
            f"[{r.get('title') or r.get('note_id')}]\n{str(r.get('body') or '')[:300]}"
            for r in results[:limit]
        )

    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> list[dict]:
        conn = self._read_conn()
        if conn is None:
            return self._locked(lambda: self.store.search(query, limit=limit, scopes=scopes))
        return self._read_search(query, limit=limit, scopes=scopes)

    def _read_search(
        self,
        query: str,
        *,
        limit: int = 5,
        scopes: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        conn = self._read_conn()
        if conn is None:
            return self.store.search(query, limit=limit, scopes=scopes, include_kinds=include_kinds)
        return search_notes(
            conn, FTSSearch(conn), self._read_vector_store(conn), query,
            limit=limit, scopes=scopes, include_kinds=include_kinds,
            fts_weight=self.store.fts_weight, vec_weight=self.store.vec_weight,
        )

    def update(
        self,
        note_id: str,
        content: str,
        *,
        principal: RuntimePrincipal | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        return self._locked(lambda: self.store.update_note(note_id, content, tags))

    def delete(self, note_id: str, *, principal: RuntimePrincipal | None = None) -> bool:
        return self._locked(lambda: self.store.delete_note(note_id))

    def promote(
        self,
        note_id: str,
        target_scope: str,
        *,
        principal: RuntimePrincipal | None = None,
    ) -> bool:
        def _do() -> bool:
            note = self.store.get_note(note_id)
            if not note or note_id not in visible_note_ids(self.store.conn, [note_id]):
                return False
            content = str(note.get("body") or note.get("content") or "")
            title = str(note.get("title") or "")
            tags = list(note.get("tags") or [])
            self.store.remember(content, title=title, tags=tags, scope=target_scope)
            return True

        return self._locked(_do)
