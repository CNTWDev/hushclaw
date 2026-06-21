"""Typed models for search/runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchIntent:
    goal: str
    queries: list[str] = field(default_factory=list)
    domain_hints: list[str] = field(default_factory=list)
    locale: str = ""
    freshness: str = ""
    max_queries: int = 4
    max_urls: int = 12
    per_query_limit: int = 5
    read_mode: str = "mixed"


@dataclass
class SearchPlan:
    goal: str
    queries: list[str]
    domain_hints: list[str] = field(default_factory=list)
    max_urls: int = 12
    per_query_limit: int = 5
    read_mode: str = "mixed"
    locale: str = ""
    freshness: str = ""


@dataclass
class SearchResultItem:
    title: str
    url: str
    snippet: str = ""
    content_preview: str = ""
    published_at: str = ""
    source_query: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content_preview": self.content_preview,
            "published_at": self.published_at,
            "source_query": self.source_query,
        }


@dataclass
class SearchBatchRequest:
    queries: list[str]
    limit: int = 5
    locale: str = ""
    freshness: str = ""
    timeout: int = 12


@dataclass
class SearchBatchResult:
    results: list[SearchResultItem] = field(default_factory=list)
    cache_hits: int = 0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "results": [item.to_dict() for item in self.results],
            "cache_hits": self.cache_hits,
            "failures": list(self.failures),
        }


@dataclass
class ReadResultItem:
    url: str
    mode: str
    content: str = ""
    title: str = ""
    error: str = ""
    from_cache: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "mode": self.mode,
            "content": self.content,
            "title": self.title,
            "error": self.error,
            "from_cache": self.from_cache,
        }


@dataclass
class ReadBatchRequest:
    urls: list[str]
    mode: str = "mixed"
    timeout: int = 10


@dataclass
class ReadBatchResult:
    items: list[ReadResultItem] = field(default_factory=list)
    cache_hits: int = 0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "cache_hits": self.cache_hits,
            "failures": list(self.failures),
        }


@dataclass
class ResearchRequest:
    goal: str
    queries: list[str] = field(default_factory=list)
    domain_hints: list[str] = field(default_factory=list)
    locale: str = ""
    freshness: str = ""
    max_urls: int = 12
    per_query_limit: int = 5
    read_mode: str = "mixed"
    interactive: bool = True


@dataclass
class ResearchTelemetry:
    planned_queries: int = 0
    urls_selected: int = 0
    search_cache_hits: int = 0
    read_cache_hits: int = 0
    search_failures: int = 0
    read_failures: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "planned_queries": self.planned_queries,
            "urls_selected": self.urls_selected,
            "search_cache_hits": self.search_cache_hits,
            "read_cache_hits": self.read_cache_hits,
            "search_failures": self.search_failures,
            "read_failures": self.read_failures,
        }


@dataclass
class ResearchResult:
    plan: SearchPlan
    search: SearchBatchResult
    read: ReadBatchResult
    telemetry: ResearchTelemetry
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.plan.goal,
            "plan": {
                "queries": list(self.plan.queries),
                "domain_hints": list(self.plan.domain_hints),
                "max_urls": self.plan.max_urls,
                "per_query_limit": self.plan.per_query_limit,
                "read_mode": self.plan.read_mode,
                "locale": self.plan.locale,
                "freshness": self.plan.freshness,
            },
            "summary": self.summary,
            "search": self.search.to_dict(),
            "read": self.read.to_dict(),
            "telemetry": self.telemetry.to_dict(),
        }
