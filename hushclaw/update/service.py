"""Update check service with semantic-version comparison and cache."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from importlib import metadata

from hushclaw.update.provider import GithubReleaseProvider, NoReleasesError, ReleaseInfo, UpdateProvider


_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


@dataclass
class _ParsedVersion:
    major: int
    minor: int
    patch: int
    is_prerelease: bool = False


def detect_current_version() -> str:
    """Resolve current version from package metadata with fallback."""
    try:
        return metadata.version("hushclaw")
    except Exception:
        pass
    try:
        from hushclaw import __version__

        return str(__version__)
    except Exception:
        return "0.0.0"


def parse_version(version: str) -> _ParsedVersion | None:
    """Parse versions like v1.2.3, 1.2.3-rc1, 1.2.3+meta."""
    m = _SEMVER_RE.match((version or "").strip())
    if not m:
        return None
    major, minor, patch = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    is_prerelease = "-" in (version or "")
    return _ParsedVersion(major=major, minor=minor, patch=patch, is_prerelease=is_prerelease)


def compare_versions(a: str, b: str) -> int | None:
    """Return -1/0/1 if a < b / == / > b, or None when unparsable."""
    pa = parse_version(a)
    pb = parse_version(b)
    if pa is None or pb is None:
        return None
    ta = (pa.major, pa.minor, pa.patch)
    tb = (pb.major, pb.minor, pb.patch)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    # Stable release outranks prerelease for same numeric tuple.
    if pa.is_prerelease and not pb.is_prerelease:
        return -1
    if not pa.is_prerelease and pb.is_prerelease:
        return 1
    return 0


class UpdateService:
    """Checks for updates with bounded cache and runtime status state."""

    def __init__(
        self,
        provider: UpdateProvider | None = None,
        current_version: str | None = None,
        cache_ttl_seconds: int = 900,
    ) -> None:
        self._provider = provider or GithubReleaseProvider()
        self._current_version = current_version or detect_current_version()
        self._cache_ttl_seconds = max(30, cache_ttl_seconds)
        self._cache_by_channel: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()
        self._last_result: dict | None = None
        self._last_checked_at: int = 0

    @property
    def current_version(self) -> str:
        return self._current_version

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def last_checked_at(self) -> int:
        return self._last_checked_at

    async def check_for_update(self, include_prerelease: bool = False, force: bool = False) -> dict:
        channel = "prerelease" if include_prerelease else "stable"
        now = time.monotonic()
        cached = self._cache_by_channel.get(channel)
        if cached and not force:
            ts, result = cached
            if now - ts < self._cache_ttl_seconds:
                out = dict(result)
                out["cached"] = True
                return out

        async with self._lock:
            # Re-check cache once under lock to avoid duplicate outbound requests.
            cached = self._cache_by_channel.get(channel)
            if cached and not force:
                ts, result = cached
                if now - ts < self._cache_ttl_seconds:
                    out = dict(result)
                    out["cached"] = True
                    return out
            result = await self._fetch_once(include_prerelease=include_prerelease)
            self._cache_by_channel[channel] = (time.monotonic(), result)
            self._last_result = dict(result)
            self._last_checked_at = int(time.time())
            return dict(result)

    async def _fetch_once(self, include_prerelease: bool) -> dict:
        try:
            release: ReleaseInfo = await self._provider.fetch_latest(
                include_prerelease=include_prerelease
            )
        except NoReleasesError:
            # Repo has no releases/tags yet — treat as already up to date.
            return {
                "ok": True,
                "type": "update_status",
                "current_version": self._current_version,
                "latest_version": self._current_version,
                "update_available": False,
                "compare_result": 0,
                "release_url": "",
                "published_at": "",
                "error": "",
                "channel": "prerelease" if include_prerelease else "stable",
            }
        except Exception as exc:
            return {
                "ok": False,
                "type": "update_status",
                "current_version": self._current_version,
                "latest_version": "",
                "update_available": False,
                "compare_result": None,
                "release_url": "",
                "published_at": "",
                "error": str(exc),
                "channel": "prerelease" if include_prerelease else "stable",
                "cached": False,
            }

        cmp_res = compare_versions(self._current_version, release.version)
        update_available = (cmp_res == -1)
        return {
            "ok": True,
            "type": "update_status",
            "current_version": self._current_version,
            "latest_version": release.version,
            "update_available": update_available,
            "compare_result": cmp_res,
            "release_url": release.html_url,
            "published_at": release.published_at,
            "prerelease": release.prerelease,
            "channel": "prerelease" if include_prerelease else "stable",
            "cached": False,
            "error": "",
        }
