import asyncio
import json
from types import SimpleNamespace

from hushclaw.search import (
    ReadBatchRequest,
    ResearchRequest,
    ResearchRuntime,
    SearchBatchRequest,
    SearchPlanner,
    SearchService,
)
from hushclaw.search.models import ReadBatchResult, ReadResultItem, SearchBatchResult, SearchPlan, SearchResultItem


def test_search_planner_builds_default_queries():
    planner = SearchPlanner()
    plan = planner.plan(SimpleNamespace(
        goal="AI agent runtime trends",
        queries=[],
        domain_hints=[],
        locale="",
        freshness="",
        max_queries=4,
        max_urls=12,
        per_query_limit=5,
        read_mode="mixed",
    ))
    assert plan.goal == "AI agent runtime trends"
    assert plan.queries
    assert plan.queries[0] == "AI agent runtime trends"
    assert len(plan.queries) <= 4


def test_search_service_search_batch_dedups_and_counts_cache_hits():
    class FakeSearchProvider:
        def __init__(self):
            self.calls = 0

        def search(self, query, **kwargs):
            self.calls += 1
            if query == "q1":
                return ({
                    "results": [
                        {"title": "A", "url": "https://example.com/a", "snippet": "one"},
                        {"title": "B", "url": "https://example.com/b", "snippet": "two"},
                    ]
                }, True)
            return ({
                "results": [
                    {"title": "A dup", "url": "https://example.com/a", "snippet": "dup"},
                        {"title": "C", "url": "https://example.com/c", "snippet": "three"},
                    ]
                }, False)

    service = SearchService(search_provider=FakeSearchProvider())
    result = asyncio.run(service.search_batch(SearchBatchRequest(queries=["q1", "q2"])))
    assert result.cache_hits == 1
    assert [item.url for item in result.results] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_search_service_read_batch_falls_back_to_fetch():
    class FakeReaderProvider:
        def read(self, url, **kwargs):
            raise RuntimeError("reader failed")

    class FakeFetchProvider:
        def fetch(self, url, **kwargs):
            return "fetched content", False

    service = SearchService(reader_provider=FakeReaderProvider(), fetch_provider=FakeFetchProvider())
    result = asyncio.run(service.read_batch(ReadBatchRequest(urls=["https://example.com/a"], mode="mixed")))
    assert result.failures == []
    assert result.items[0].mode == "fetch"
    assert result.items[0].content == "fetched content"


def test_research_web_tool_returns_structured_payload():
    from hushclaw.tools.builtins.research_tools import research_web

    class FakeRuntime:
        async def run(self, request, **kwargs):
            from hushclaw.search.models import (
                ReadBatchResult,
                ResearchResult,
                ResearchTelemetry,
                SearchBatchResult,
                SearchPlan,
            )

            return ResearchResult(
                plan=SearchPlan(goal=request.goal, queries=["q1"], max_urls=2, per_query_limit=5),
                search=SearchBatchResult(results=[
                    SearchResultItem(title="A", url="https://example.com/a", snippet="snippet")
                ]),
                read=ReadBatchResult(items=[
                    ReadResultItem(url="https://example.com/a", mode="reader", content="body")
                ]),
                telemetry=ResearchTelemetry(planned_queries=1, urls_selected=1),
                summary="Research corpus for: test",
            )

    import hushclaw.tools.builtins.research_tools as research_tools_mod

    original = research_tools_mod.get_research_runtime
    research_tools_mod.get_research_runtime = lambda: FakeRuntime()
    try:
        result = asyncio.run(research_web("test"))
    finally:
        research_tools_mod.get_research_runtime = original

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["goal"] == "test"
    assert payload["plan"]["queries"] == ["q1"]
    assert payload["telemetry"]["urls_selected"] == 1


def test_research_runtime_emits_progress_events():
    class FakeSearchService:
        async def search_batch(self, request, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb is not None:
                await cb({"completed": 1, "total": 2, "results": 3, "cache_hits": 1, "failures": 0})
                await cb({"completed": 2, "total": 2, "results": 4, "cache_hits": 1, "failures": 0})
            return SearchBatchResult(results=[
                SearchResultItem(title="A", url="https://example.com/a"),
                SearchResultItem(title="B", url="https://example.com/b"),
            ], cache_hits=1)

        async def read_batch(self, request, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb is not None:
                await cb({"completed": 1, "total": 2, "ok": 1, "cache_hits": 0, "failures": 0})
                await cb({"completed": 2, "total": 2, "ok": 2, "cache_hits": 1, "failures": 0})
            return ReadBatchResult(items=[
                ReadResultItem(url="https://example.com/a", mode="reader", content="body"),
                ReadResultItem(url="https://example.com/b", mode="reader", content="body"),
            ], cache_hits=1)

    class FakePlanner:
        def plan(self, intent):
            return SearchPlan(
                goal=intent.goal,
                queries=["q1", "q2"],
                max_urls=2,
                per_query_limit=5,
                read_mode="mixed",
            )

    runtime = ResearchRuntime(search_service=FakeSearchService(), planner=FakePlanner())
    events: list[tuple[str, dict]] = []

    async def progress(event_type, payload):
        events.append((event_type, dict(payload)))

    result = asyncio.run(runtime.run(ResearchRequest(goal="test goal"), progress_callback=progress))
    assert result.telemetry.urls_selected == 2
    kinds = [event_type for event_type, _ in events]
    assert kinds[0] == "research_job_started"
    assert "research_queries_planned" in kinds
    assert "research_search_progress" in kinds
    assert "research_read_progress" in kinds
    assert kinds[-1] == "research_job_completed"
