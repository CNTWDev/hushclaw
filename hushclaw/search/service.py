"""Search execution service with batching, dedup, and cache-aware provider use."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from hushclaw.search.dedup import canonicalize_url, dedup_urls
from hushclaw.search.models import (
    ReadBatchRequest,
    ReadBatchResult,
    ReadResultItem,
    SearchBatchRequest,
    SearchBatchResult,
    SearchResultItem,
)
from hushclaw.search.providers import JinaReaderProvider, JinaSearchProvider, LocalFetchProvider


class SearchService:
    def __init__(
        self,
        *,
        search_provider: JinaSearchProvider | None = None,
        reader_provider: JinaReaderProvider | None = None,
        fetch_provider: LocalFetchProvider | None = None,
        query_concurrency: int = 6,
        read_concurrency: int = 8,
        per_domain_concurrency: int = 2,
    ) -> None:
        self.search_provider = search_provider or JinaSearchProvider()
        self.reader_provider = reader_provider or JinaReaderProvider()
        self.fetch_provider = fetch_provider or LocalFetchProvider()
        self.query_concurrency = max(1, query_concurrency)
        self.read_concurrency = max(1, read_concurrency)
        self.per_domain_concurrency = max(1, per_domain_concurrency)
        self._domain_locks: dict[str, asyncio.Semaphore] = {}

    async def search_batch(
        self,
        request: SearchBatchRequest,
        *,
        _config=None,
        _credential_service=None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> SearchBatchResult:
        sem = asyncio.Semaphore(self.query_concurrency)
        result = SearchBatchResult()
        completed = 0

        async def _run(query: str) -> None:
            nonlocal completed
            async with sem:
                try:
                    payload, from_cache = await asyncio.to_thread(
                        self.search_provider.search,
                        query,
                        limit=request.limit,
                        timeout=request.timeout,
                        locale=request.locale,
                        freshness=request.freshness,
                        _config=_config,
                        _credential_service=_credential_service,
                    )
                    if from_cache:
                        result.cache_hits += 1
                    for item in payload.get("results", []) if isinstance(payload, dict) else []:
                        url = str(item.get("url") or "").strip()
                        if not url:
                            continue
                        result.results.append(
                            SearchResultItem(
                                title=str(item.get("title") or "").strip(),
                                url=url,
                                snippet=str(item.get("snippet") or "").strip(),
                                content_preview=str(item.get("content_preview") or "").strip(),
                                published_at=str(item.get("published_at") or "").strip(),
                                source_query=query,
                            )
                        )
                except Exception as exc:
                    result.failures.append(f"{query}: {exc}")
                finally:
                    completed += 1
                    await self._emit_progress(
                        progress_callback,
                        {
                            "kind": "search",
                            "completed": completed,
                            "total": len([q for q in request.queries if str(q or "").strip()]),
                            "query": query,
                            "results": len(result.results),
                            "cache_hits": result.cache_hits,
                            "failures": len(result.failures),
                        },
                    )

        await asyncio.gather(*(_run(query) for query in request.queries if str(query or "").strip()))

        deduped: list[SearchResultItem] = []
        seen: set[str] = set()
        for item in result.results:
            key = canonicalize_url(item.url)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        result.results = deduped
        return result

    async def read_batch(
        self,
        request: ReadBatchRequest,
        *,
        _config=None,
        _credential_service=None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> ReadBatchResult:
        sem = asyncio.Semaphore(self.read_concurrency)
        result = ReadBatchResult()
        completed = 0

        async def _run(url: str) -> None:
            nonlocal completed
            async with sem:
                domain_key = canonicalize_url(url).split("/", 3)[2] if "://" in canonicalize_url(url) else ""
                domain_sem = self._domain_locks.setdefault(domain_key, asyncio.Semaphore(self.per_domain_concurrency))
                async with domain_sem:
                    item = await self._read_one(
                        url,
                        mode=request.mode,
                        timeout=request.timeout,
                        _config=_config,
                        _credential_service=_credential_service,
                    )
                    if item.from_cache:
                        result.cache_hits += 1
                    if item.error:
                        result.failures.append(f"{url}: {item.error}")
                    result.items.append(item)
                    completed += 1
                    ok_items = sum(1 for existing in result.items if not existing.error)
                    await self._emit_progress(
                        progress_callback,
                        {
                            "kind": "read",
                            "completed": completed,
                            "total": len(urls),
                            "url": url,
                            "ok": ok_items,
                            "cache_hits": result.cache_hits,
                            "failures": len(result.failures),
                        },
                    )

        urls = dedup_urls(request.urls)
        await asyncio.gather(*(_run(url) for url in urls))
        return result

    async def _read_one(
        self,
        url: str,
        *,
        mode: str,
        timeout: int,
        _config=None,
        _credential_service=None,
    ) -> ReadResultItem:
        mode = (mode or "mixed").strip().lower()
        if mode not in {"reader", "fetch", "mixed"}:
            mode = "mixed"
        if mode in {"reader", "mixed"}:
            try:
                content, from_cache = await asyncio.to_thread(
                    self.reader_provider.read,
                    url,
                    timeout=timeout,
                    _config=_config,
                    _credential_service=_credential_service,
                )
                return ReadResultItem(url=url, mode="reader", content=content, from_cache=from_cache)
            except Exception as exc:
                if mode == "reader":
                    return ReadResultItem(url=url, mode="reader", error=str(exc))
        try:
            content, from_cache = await asyncio.to_thread(
                self.fetch_provider.fetch,
                url,
                timeout=timeout,
            )
            return ReadResultItem(url=url, mode="fetch", content=content, from_cache=from_cache)
        except Exception as exc:
            return ReadResultItem(url=url, mode="fetch", error=str(exc))

    @staticmethod
    async def _emit_progress(
        callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        payload: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        try:
            maybe = callback(payload)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:
            return


_DEFAULT_SEARCH_SERVICE = SearchService()


def get_search_service() -> SearchService:
    return _DEFAULT_SEARCH_SERVICE
