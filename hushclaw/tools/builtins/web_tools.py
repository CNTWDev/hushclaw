"""Web tools: fetch URLs using urllib (no external deps)."""
from __future__ import annotations

import gzip
import http.cookiejar
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from hushclaw.tools.base import tool, ToolResult
from hushclaw.util.ssl_context import make_ssl_context

# ---------------------------------------------------------------------------
# Session-level state (persists within a process, resets on server restart)
# ---------------------------------------------------------------------------

# Shared cookie jar: session cookies from fetch_url calls carry over across
# requests (e.g. login → protected page), mimicking a real browser session.
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
    urllib.request.HTTPSHandler(context=make_ssl_context()),
)

# Browser-like request headers that pass most User-Agent and Accept checks.
# Sec-Fetch-* / TLS-fingerprint checks still require a real browser (use
# browser_navigate for those).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


def _decompress(raw: bytes, encoding: str) -> bytes:
    """Decompress raw bytes according to Content-Encoding."""
    enc = encoding.lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc in ("deflate", "zlib"):
        try:
            return zlib.decompress(raw)
        except zlib.error:
            # Some servers send raw deflate (no zlib wrapper)
            return zlib.decompress(raw, -15)
    return raw


def _normalize_url(url: str) -> str:
    """Normalize URLs so non-ASCII paths/queries are safe for urllib."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return url
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        pass
    netloc = host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        auth = urllib.parse.quote(parts.username, safe="")
        if parts.password:
            auth = f"{auth}:{urllib.parse.quote(parts.password, safe='')}"
        netloc = f"{auth}@{netloc}"
    if ":" in host and not host.startswith("["):
        # IPv6 literals must stay bracketed inside netloc.
        netloc = parts.netloc
    path = urllib.parse.quote(parts.path or "", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query or "", safe="=&%:@!$'()*+,;/?-._~")
    fragment = urllib.parse.quote(parts.fragment or "", safe="%:@!$&'()*+,;=/?-._~")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, fragment))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    name="fetch_url",
    description=(
        "Fetch the content of a URL and return the response body as text. "
        "Uses browser-like headers (Chrome User-Agent, Accept, Accept-Language) "
        "and a shared cookie jar so session cookies persist across requests. "
        "Automatically decompresses gzip/deflate responses. "
        "Retries on 429 (rate-limit) and 503 (overload) with brief back-off. "
        "For JS-rendered pages or Cloudflare-protected sites use browser_navigate. "
        "For clean LLM-friendly markdown output use jina_read."
    ),
)
def fetch_url(
    url: str,
    max_bytes: int = 65536,
    timeout: int = 20,
) -> ToolResult:
    """Fetch a URL using browser-like headers with cookie and gzip support."""
    if not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
    url = _normalize_url(url)

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=dict(_BROWSER_HEADERS))
            with _opener.open(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                enc = (resp.headers.get("Content-Encoding") or "").strip()
                if enc:
                    try:
                        raw = _decompress(raw, enc)
                    except Exception:
                        pass  # keep raw bytes on decompress failure
                truncated = len(raw) > max_bytes
                if truncated:
                    raw = raw[:max_bytes]
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                if truncated:
                    text = f"[Content truncated at {max_bytes} bytes]\n{text}"
                return ToolResult.ok(text)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 ** attempt)   # 1 s, then 2 s back-off
                continue
            return ToolResult.error(f"HTTP {e.code} fetching {url}: {e.reason}")
        except urllib.error.URLError as e:
            return ToolResult.error(f"URL error fetching {url}: {e.reason}")
        except Exception as e:
            return ToolResult.error(f"Failed to fetch {url}: {e}")
    return ToolResult.error(f"Gave up after retries: {url}")


@tool(
    name="jina_read",
    description=(
        "Fetch a URL via Jina Reader (r.jina.ai) and return clean, structured markdown. "
        "Jina's servers render the page (including JavaScript), strip ads/nav/boilerplate, "
        "and return article text, headings, and links — far more token-efficient than raw HTML. "
        "Works on many JS-heavy SPAs, some paywalled articles, and sites that block "
        "plain urllib requests. Free tier: ~200 req/day at 1 req/min. "
        "Pass jina_api_key for paid tier (no rate limit). "
        "For interactive automation or login flows use browser_navigate instead."
    ),
)
def jina_read(
    url: str,
    timeout: int = 30,
    jina_api_key: str = "",
) -> ToolResult:
    """Fetch clean markdown via Jina Reader API."""
    if not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
    url = _normalize_url(url)
    jina_url = f"https://r.jina.ai/{url}"
    headers: dict[str, str] = {
        "User-Agent": "HushClaw/1.0",
        "Accept": "text/plain, application/json",
        "X-Return-Format": "markdown",
        "X-Timeout": str(max(5, timeout - 5)),
    }
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"
    try:
        req = urllib.request.Request(jina_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            raw = resp.read(131072)  # 128 KB max
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                return ToolResult.error(f"Jina returned empty content for: {url}")
            return ToolResult.ok(text)
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return ToolResult.error(
                f"Jina could not process {url}: unsupported or inaccessible content"
            )
        if e.code == 429:
            return ToolResult.error(
                "Jina rate limit reached (free tier: 1 req/min). "
                "Wait a minute or pass a jina_api_key for the paid tier."
            )
        return ToolResult.error(f"Jina HTTP {e.code} for {url}: {e.reason}")
    except urllib.error.URLError as e:
        return ToolResult.error(f"Jina URL error for {url}: {e.reason}")
    except Exception as e:
        return ToolResult.error(f"Jina read failed for {url}: {e}")
