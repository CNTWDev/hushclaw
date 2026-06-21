"""Shared in-process caches for web retrieval."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        expires_at = time.time() + max(0.0, float(ttl_seconds))
        with self._lock:
            self._entries[key] = _CacheEntry(value=value, expires_at=expires_at)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


@dataclass
class SharedSearchCaches:
    query: TTLCache
    content: TTLCache
    negative: TTLCache

    def clear(self) -> None:
        self.query.clear()
        self.content.clear()
        self.negative.clear()


_SHARED = SharedSearchCaches(query=TTLCache(), content=TTLCache(), negative=TTLCache())


def get_shared_search_caches() -> SharedSearchCaches:
    return _SHARED


def clear_shared_search_caches() -> None:
    _SHARED.clear()
