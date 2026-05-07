"""GitHub app connector tools."""
from __future__ import annotations

from hushclaw.secrets import get_secret_store
from hushclaw.tools.base import ToolResult, tool


def _cfg(_config):
    gh = getattr(getattr(_config, "app_connectors", None), "github", None)
    if gh is None:
        raise ValueError("GitHub app connector config is unavailable")
    return gh


@tool(
    name="github_search",
    description=(
        "Search the connected GitHub repository for issues, pull requests, code, commits, or repositories. "
        "Use when the user asks about repo work, bugs, PRs, issues, commits, CI, or code hosted on GitHub. "
        "Returns JSON with a summary and sources metadata."
    ),
    timeout=30,
    parallel_safe=True,
)
def github_search(query: str, search_type: str = "issues", repo: str = "", limit: int = 5, _config=None) -> ToolResult:
    from hushclaw.app_connectors.github import search

    return search(_cfg(_config), get_secret_store(), query, search_type=search_type, repo=repo, limit=limit)


@tool(
    name="github_read",
    description=(
        "Read a connected GitHub issue, pull request, or repository file. "
        "Use an issue/PR number like '123' or a file path like 'README.md'. "
        "Returns JSON with content and sources metadata."
    ),
    timeout=30,
    parallel_safe=True,
)
def github_read(target: str, repo: str = "", kind: str = "auto", _config=None) -> ToolResult:
    from hushclaw.app_connectors.github import read

    return read(_cfg(_config), get_secret_store(), target, repo=repo, kind=kind)
