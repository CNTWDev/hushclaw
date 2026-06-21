"""Research-oriented orchestration on top of SearchService."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from hushclaw.search.dedup import dedup_urls
from hushclaw.search.models import (
    ReadBatchRequest,
    ResearchRequest,
    ResearchResult,
    ResearchTelemetry,
    SearchBatchRequest,
    SearchIntent,
)
from hushclaw.search.planner import SearchPlanner
from hushclaw.search.service import SearchService, get_search_service


class ResearchRuntime:
    def __init__(
        self,
        *,
        search_service: SearchService | None = None,
        planner: SearchPlanner | None = None,
    ) -> None:
        self.search_service = search_service or get_search_service()
        self.planner = planner or SearchPlanner()

    async def run(
        self,
        request: ResearchRequest,
        *,
        _config=None,
        _credential_service=None,
        progress_callback: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> ResearchResult:
        try:
            await self._emit_progress(
                progress_callback,
                "research_job_started",
                {
                    "goal": request.goal,
                    "interactive": bool(request.interactive),
                    "max_urls": int(request.max_urls or 0),
                    "read_mode": str(request.read_mode or "mixed"),
                },
            )
            plan = self.planner.plan(
                SearchIntent(
                    goal=request.goal,
                    queries=request.queries,
                    domain_hints=request.domain_hints,
                    locale=request.locale,
                    freshness=request.freshness,
                    max_urls=request.max_urls,
                    per_query_limit=request.per_query_limit,
                    read_mode=request.read_mode,
                )
            )
            await self._emit_progress(
                progress_callback,
                "research_queries_planned",
                {
                    "goal": request.goal,
                    "queries": list(plan.queries),
                    "planned_queries": len(plan.queries),
                    "max_urls": int(plan.max_urls or 0),
                    "per_query_limit": int(plan.per_query_limit or 0),
                    "read_mode": str(plan.read_mode or "mixed"),
                },
            )
            search = await self.search_service.search_batch(
                SearchBatchRequest(
                    queries=plan.queries,
                    limit=plan.per_query_limit,
                    locale=plan.locale,
                    freshness=plan.freshness,
                ),
                _config=_config,
                _credential_service=_credential_service,
                progress_callback=lambda payload: self._emit_progress(
                    progress_callback,
                    "research_search_progress",
                    {
                        **payload,
                        "goal": request.goal,
                        "queries": list(plan.queries),
                    },
                ),
            )
            selected_urls = dedup_urls([item.url for item in search.results])[: plan.max_urls]
            read = await self.search_service.read_batch(
                ReadBatchRequest(urls=selected_urls, mode=plan.read_mode),
                _config=_config,
                _credential_service=_credential_service,
                progress_callback=lambda payload: self._emit_progress(
                    progress_callback,
                    "research_read_progress",
                    {
                        **payload,
                        "goal": request.goal,
                        "urls_selected": len(selected_urls),
                    },
                ),
            )
            telemetry = ResearchTelemetry(
                planned_queries=len(plan.queries),
                urls_selected=len(selected_urls),
                search_cache_hits=search.cache_hits,
                read_cache_hits=read.cache_hits,
                search_failures=len(search.failures),
                read_failures=len(read.failures),
            )
            summary = self._build_summary(plan.goal, search_results=search.results, read_items=read.items)
            result = ResearchResult(
                plan=plan,
                search=search,
                read=read,
                telemetry=telemetry,
                summary=summary,
            )
            await self._emit_progress(
                progress_callback,
                "research_job_completed",
                {
                    "goal": request.goal,
                    "summary": summary,
                    "telemetry": telemetry.to_dict(),
                    "queries": list(plan.queries),
                },
            )
            return result
        except Exception as exc:
            await self._emit_progress(
                progress_callback,
                "research_job_failed",
                {
                    "goal": request.goal,
                    "error": str(exc),
                },
            )
            raise

    @staticmethod
    def _build_summary(goal: str, *, search_results, read_items) -> str:
        lines = [f"Research corpus for: {goal}"]
        if search_results:
            lines.append(f"- Queries resolved into {len(search_results)} unique candidate sources.")
        if read_items:
            ok_items = [item for item in read_items if not item.error]
            lines.append(f"- Retrieved readable content from {len(ok_items)} sources.")
        return "\n".join(lines)

    @staticmethod
    async def _emit_progress(
        callback: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        try:
            maybe = callback(event_type, payload)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:
            return


_DEFAULT_RUNTIME = ResearchRuntime()


def get_research_runtime() -> ResearchRuntime:
    return _DEFAULT_RUNTIME
