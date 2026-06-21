"""Batch-oriented research tools built on the search runtime."""

from __future__ import annotations

import json
from typing import Any

from hushclaw.server.session import publish_session_event

from hushclaw.search import (
    ReadBatchRequest,
    ResearchRequest,
    SearchBatchRequest,
    get_research_runtime,
    get_search_service,
)
from hushclaw.tools.base import ToolResult, tool


@tool(
    name="research_web",
    description=(
        "Run a research-oriented web retrieval job: plan queries, batch search, batch read, "
        "deduplicate URLs, and return a compact structured corpus for the model to synthesize. "
        "Prefer this over repeated web_search/jina_read loops when you need multiple sources."
    ),
    parallel_safe=True,
    timeout=90,
)
async def research_web(
    goal: str,
    queries: list[str] | None = None,
    max_urls: int = 12,
    per_query_limit: int = 5,
    read_mode: str = "mixed",
    locale: str = "",
    freshness: str = "",
    _runtime=None,
    _config=None,
    _credential_service=None,
) -> ToolResult:
    runtime = get_research_runtime()
    progress = _research_progress_publisher(_runtime)
    result = await runtime.run(
        ResearchRequest(
            goal=goal,
            queries=list(queries or []),
            max_urls=max_urls,
            per_query_limit=per_query_limit,
            read_mode=read_mode,
            locale=locale,
            freshness=freshness,
        ),
        progress_callback=progress,
        _config=_config,
        _credential_service=_credential_service,
    )
    return ToolResult.ok(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


@tool(
    name="search_batch",
    description="Run multiple web search queries in parallel and return deduplicated normalized results.",
    parallel_safe=True,
    timeout=60,
)
async def search_batch(
    queries: list[str],
    limit: int = 5,
    locale: str = "",
    freshness: str = "",
    _config=None,
    _credential_service=None,
) -> ToolResult:
    service = get_search_service()
    result = await service.search_batch(
        SearchBatchRequest(
            queries=list(queries or []),
            limit=limit,
            locale=locale,
            freshness=freshness,
        ),
        _config=_config,
        _credential_service=_credential_service,
    )
    return ToolResult.ok(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def _research_progress_publisher(_runtime) -> Any:
    if _runtime is None:
        return None
    entry = _runtime.get("_current_session_entry")
    session_id = str(_runtime.get("_session_id") or "")
    run_id = str(_runtime.get("_current_run_id") or "")
    thread_id = str(_runtime.get("_current_thread_id") or "")

    async def _emit(event_type: str, payload: dict[str, Any]) -> None:
        if entry is None:
            return
        event = {
            "type": event_type,
            "session_id": session_id,
            "run_id": run_id,
            "thread_id": thread_id,
            **dict(payload or {}),
        }
        await publish_session_event(entry, event)

    return _emit


@tool(
    name="read_batch",
    description="Read multiple URLs in parallel using reader/fetch/mixed mode and return compact structured results.",
    parallel_safe=True,
    timeout=90,
)
async def read_batch(
    urls: list[str],
    mode: str = "mixed",
    _config=None,
    _credential_service=None,
) -> ToolResult:
    service = get_search_service()
    result = await service.read_batch(
        ReadBatchRequest(urls=list(urls or []), mode=mode),
        _config=_config,
        _credential_service=_credential_service,
    )
    return ToolResult.ok(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
