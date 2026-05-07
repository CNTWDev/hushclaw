"""GitHub App Connector."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.tools.base import ToolResult

API = "https://api.github.com"


def _request(token: str, path: str, *, method: str = "GET") -> tuple[int, dict | list | str]:
    req = urllib.request.Request(
        API + path,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HushClaw-AppConnector/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _err(status: int, payload) -> ToolResult:
    msg = payload.get("message") if isinstance(payload, dict) else str(payload)
    return ToolResult.error(f"GitHub API error {status}: {msg or 'request failed'}")


def _source(repo: str, typ: str, title: str, url: str = "", number: int | None = None, path: str = "") -> dict:
    out = {"provider": "github", "repo": repo, "type": typ, "title": title}
    if url:
        out["url"] = url
    if number is not None:
        out["number"] = number
    if path:
        out["path"] = path
    return out


class GitHubAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="github",
        name="GitHub",
        description="Search and read GitHub issues, pull requests, code, commits, and workflow runs.",
        capabilities=["search", "read"],
    )

    def configured(self) -> bool:
        token_ref = getattr(self.config, "token_ref", "app_connectors.github.token")
        return bool(self.secrets.get(token_ref))

    def tools(self):
        from hushclaw.tools.builtins import github_tools

        return [
            github_tools.github_search._hushclaw_tool,
            github_tools.github_read._hushclaw_tool,
        ]


def test_github_connection(config, secrets) -> dict:
    token_ref = getattr(config, "token_ref", "app_connectors.github.token")
    token = secrets.get(token_ref)
    repo = str(getattr(config, "default_repo", "") or "").strip()
    if not token:
        return {"ok": False, "message": "GitHub token is not set."}
    status, me = _request(token, "/user")
    if status >= 400:
        return {"ok": False, "message": f"Token check failed: {me.get('message') if isinstance(me, dict) else me}"}
    if repo:
        status, repo_payload = _request(token, f"/repos/{repo}")
        if status >= 400:
            return {"ok": False, "message": f"Repo access failed for {repo}: {repo_payload.get('message') if isinstance(repo_payload, dict) else repo_payload}"}
    login = me.get("login", "unknown") if isinstance(me, dict) else "unknown"
    return {"ok": True, "message": f"Connected as {login}" + (f"; repo {repo} is accessible." if repo else ".")}


def search(config, secrets, query: str, search_type: str = "issues", repo: str = "", limit: int = 5) -> ToolResult:
    token_ref = getattr(config, "token_ref", "app_connectors.github.token")
    token = secrets.get(token_ref)
    default_repo = str(getattr(config, "default_repo", "") or "").strip()
    target_repo = (repo or default_repo).strip()
    if not getattr(config, "enabled", False):
        return ToolResult.error("GitHub app connector is disabled. Enable it in Settings and start a new chat.")
    if not token:
        return ToolResult.error("GitHub token is not configured.")
    if not target_repo:
        return ToolResult.error("GitHub default repo is not configured.")
    q = str(query or "").strip()
    if not q:
        return ToolResult.error("query is required")
    limit = max(1, min(int(limit or 5), 10))
    search_type = (search_type or "issues").strip().lower()
    if search_type not in {"issues", "code", "commits", "repositories"}:
        search_type = "issues"
    full_q = q if search_type == "repositories" else f"{q} repo:{target_repo}"
    path = f"/search/{search_type}?q={urllib.parse.quote(full_q)}&per_page={limit}"
    status, payload = _request(token, path)
    if status >= 400:
        return _err(status, payload)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    sources = []
    compact = []
    for item in items[:limit]:
        if search_type == "issues":
            typ = "pull_request" if item.get("pull_request") else "issue"
            title = item.get("title", "")
            number = item.get("number")
            html_url = item.get("html_url", "")
            compact.append(f"- {typ} #{number}: {title}\n  {html_url}")
            sources.append(_source(target_repo, typ, title, html_url, number=number))
        elif search_type == "code":
            path_val = item.get("path", "")
            html_url = item.get("html_url", "")
            compact.append(f"- file {path_val}\n  {html_url}")
            sources.append(_source(target_repo, "file", path_val, html_url, path=path_val))
        elif search_type == "commits":
            commit = item.get("commit", {}) if isinstance(item, dict) else {}
            title = (commit.get("message", "") or "").splitlines()[0]
            sha = item.get("sha", "")[:12]
            html_url = item.get("html_url", "")
            compact.append(f"- commit {sha}: {title}\n  {html_url}")
            sources.append(_source(target_repo, "commit", title or sha, html_url))
        else:
            full_name = item.get("full_name", "")
            html_url = item.get("html_url", "")
            compact.append(f"- repo {full_name}\n  {html_url}")
            sources.append(_source(full_name or target_repo, "repository", full_name, html_url))
    result = {
        "provider": "github",
        "query": q,
        "search_type": search_type,
        "repo": target_repo,
        "summary": "\n".join(compact) if compact else "No GitHub results found.",
        "sources": sources,
    }
    return ToolResult.ok(json.dumps(result, ensure_ascii=False, indent=2))


def read(config, secrets, target: str, repo: str = "", kind: str = "auto") -> ToolResult:
    token_ref = getattr(config, "token_ref", "app_connectors.github.token")
    token = secrets.get(token_ref)
    default_repo = str(getattr(config, "default_repo", "") or "").strip()
    target_repo = (repo or default_repo).strip()
    if not getattr(config, "enabled", False):
        return ToolResult.error("GitHub app connector is disabled. Enable it in Settings and start a new chat.")
    if not token:
        return ToolResult.error("GitHub token is not configured.")
    if not target_repo:
        return ToolResult.error("GitHub default repo is not configured.")
    target = str(target or "").strip()
    if not target:
        return ToolResult.error("target is required")
    kind = (kind or "auto").strip().lower()
    if kind in {"auto", "issue", "pull_request"} and target.lstrip("#").isdigit():
        number = int(target.lstrip("#"))
        status, payload = _request(token, f"/repos/{target_repo}/issues/{number}")
        if status >= 400:
            return _err(status, payload)
        typ = "pull_request" if payload.get("pull_request") else "issue"
        source = _source(target_repo, typ, payload.get("title", ""), payload.get("html_url", ""), number=number)
        return ToolResult.ok(json.dumps({
            "provider": "github",
            "repo": target_repo,
            "type": typ,
            "number": number,
            "title": payload.get("title", ""),
            "state": payload.get("state", ""),
            "body": payload.get("body", "") or "",
            "url": payload.get("html_url", ""),
            "sources": [source],
        }, ensure_ascii=False, indent=2))
    if kind in {"auto", "file"}:
        path = urllib.parse.quote(target, safe="/")
        status, payload = _request(token, f"/repos/{target_repo}/contents/{path}")
        if status >= 400:
            return _err(status, payload)
        source = _source(target_repo, "file", payload.get("path", target), payload.get("html_url", ""), path=payload.get("path", target))
        return ToolResult.ok(json.dumps({
            "provider": "github",
            "repo": target_repo,
            "type": "file",
            "path": payload.get("path", target),
            "name": payload.get("name", ""),
            "download_url": payload.get("download_url", ""),
            "url": payload.get("html_url", ""),
            "encoding": payload.get("encoding", ""),
            "content": payload.get("content", ""),
            "sources": [source],
        }, ensure_ascii=False, indent=2))
    return ToolResult.error("Unsupported GitHub read target. Use issue/PR number or repository file path.")
