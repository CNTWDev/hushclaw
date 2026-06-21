"""Deterministic planner for research-oriented search tasks."""

from __future__ import annotations

from hushclaw.search.models import SearchIntent, SearchPlan


class SearchPlanner:
    def plan(self, intent: SearchIntent) -> SearchPlan:
        queries = [q.strip() for q in intent.queries if str(q or "").strip()]
        goal = " ".join(str(intent.goal or "").split())
        if not queries and goal:
            queries = self._default_queries(goal, max_queries=max(1, intent.max_queries))
        queries = queries[: max(1, intent.max_queries)]
        max_urls = max(1, min(48, int(intent.max_urls or 12)))
        per_query_limit = max(1, min(10, int(intent.per_query_limit or 5)))
        return SearchPlan(
            goal=goal,
            queries=queries,
            domain_hints=[h.strip() for h in intent.domain_hints if str(h or "").strip()],
            max_urls=max_urls,
            per_query_limit=per_query_limit,
            read_mode=intent.read_mode or "mixed",
            locale=intent.locale or "",
            freshness=intent.freshness or "",
        )

    @staticmethod
    def _default_queries(goal: str, *, max_queries: int) -> list[str]:
        seeds = [
            goal,
            f"{goal} official documentation",
            f"{goal} analysis",
            f"{goal} site:github.com",
        ]
        if any("\u4e00" <= ch <= "\u9fff" for ch in goal):
            seeds = [
                goal,
                f"{goal} 官方 文档",
                f"{goal} 分析",
                f"{goal} site:github.com",
            ]
        out: list[str] = []
        seen: set[str] = set()
        for item in seeds:
            norm = " ".join(item.split())
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
            if len(out) >= max_queries:
                break
        return out
