"""Update metadata providers."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from hushclaw.util.ssl_context import make_ssl_context


@dataclass
class ReleaseInfo:
    version: str
    html_url: str
    published_at: str
    prerelease: bool = False


class NoReleasesError(RuntimeError):
    """Raised when the upstream repo has no releases or tags yet."""


class UpdateProvider:
    """Abstract release metadata provider."""

    async def fetch_latest(self, include_prerelease: bool = False) -> ReleaseInfo:
        raise NotImplementedError


class GithubReleaseProvider(UpdateProvider):
    """Fetch latest release metadata from GitHub.

    Primary source: /releases/latest (stable) or /releases?per_page=20 (prerelease).
    Fallback when no releases exist: /tags (e.g. repos that only push git tags).
    Raises NoReleasesError when neither source has any version data.
    """

    def __init__(self, owner: str = "CNTWDev", repo: str = "hushclaw", timeout: int = 8) -> None:
        self._owner = owner
        self._repo = repo
        self._timeout = timeout

    async def fetch_latest(self, include_prerelease: bool = False) -> ReleaseInfo:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_sync, include_prerelease)

    def _fetch_sync(self, include_prerelease: bool) -> ReleaseInfo:
        if include_prerelease:
            url = (
                f"https://api.github.com/repos/{self._owner}/{self._repo}/releases"
                "?per_page=20"
            )
            try:
                payload = self._get_json(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return self._fetch_from_tags()
                raise
            if not isinstance(payload, list):
                raise RuntimeError("Invalid GitHub response: expected release list")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                tag = str(item.get("tag_name", "")).strip()
                if not tag:
                    continue
                return ReleaseInfo(
                    version=tag,
                    html_url=str(item.get("html_url", "")),
                    published_at=str(item.get("published_at", "")),
                    prerelease=bool(item.get("prerelease", False)),
                )
            # Empty release list — try tags as fallback.
            return self._fetch_from_tags()

        url = f"https://api.github.com/repos/{self._owner}/{self._repo}/releases/latest"
        try:
            item = self._get_json(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Repo exists but has no formal GitHub Release yet; try git tags.
                return self._fetch_from_tags()
            raise
        if not isinstance(item, dict):
            raise RuntimeError("Invalid GitHub response: expected release object")
        tag = str(item.get("tag_name", "")).strip()
        if not tag:
            raise RuntimeError("GitHub latest release has empty tag_name")
        return ReleaseInfo(
            version=tag,
            html_url=str(item.get("html_url", "")),
            published_at=str(item.get("published_at", "")),
            prerelease=bool(item.get("prerelease", False)),
        )

    def _fetch_from_tags(self) -> ReleaseInfo:
        """Fall back to the /tags endpoint when no GitHub Releases exist."""
        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}/tags"
            "?per_page=20"
        )
        payload = self._get_json(url)
        if not isinstance(payload, list):
            raise NoReleasesError("No releases or tags found on GitHub")
        for item in payload:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("name", "")).strip()
            if not tag:
                continue
            html_url = (
                f"https://github.com/{self._owner}/{self._repo}/releases/tag/{tag}"
            )
            return ReleaseInfo(
                version=tag,
                html_url=html_url,
                published_at="",
                prerelease=False,
            )
        raise NoReleasesError("No releases or tags found on GitHub")

    def _get_json(self, url: str):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HushClaw/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(
            req,
            timeout=self._timeout,
            context=make_ssl_context(),
        ) as resp:
            raw = resp.read()
        return json.loads(raw)
