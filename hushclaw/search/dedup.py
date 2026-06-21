"""URL normalization and dedup helpers for search results."""

from __future__ import annotations

import urllib.parse


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urllib.parse.urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return raw
    netloc = host
    if parts.port:
        default_port = 443 if scheme == "https" else 80
        if parts.port != default_port:
            netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = parts.query or ""
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def dedup_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = canonicalize_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out
