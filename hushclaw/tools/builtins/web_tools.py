"""Web tools: fetch URLs using curl_cffi (Chrome TLS fingerprint) or urllib fallback."""
from __future__ import annotations

import gzip
import http.cookiejar
import importlib
import ipaddress
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from hushclaw.tools.base import tool, ToolResult
from hushclaw.util.ssl_context import make_ssl_context
from hushclaw.util.logging import get_logger

log = get_logger("web_tools")


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),    # Shared address space (RFC 6598)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_BLOCKED_HOSTNAMES = {
    "169.254.169.254",          # AWS/GCP/Azure IMDS
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",            # historical EC2 alias
}


def _check_ssrf(url: str) -> str | None:
    """Return an error string if the URL targets an internal/private resource, else None."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "Invalid URL: no hostname"
    if host in _BLOCKED_HOSTNAMES:
        return f"SSRF blocked: {host!r} is a cloud metadata endpoint"
    # Resolve DNS — fail-closed: if resolution fails we still proceed
    # (DNS failure will be caught downstream as a connection error).
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            addr_str = info[4][0]
            try:
                ip = ipaddress.ip_address(addr_str)
            except ValueError:
                continue
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    return f"SSRF blocked: {host!r} resolves to internal address {addr_str}"
    except OSError:
        pass  # DNS failure; let the actual request fail naturally
    return None


# ---------------------------------------------------------------------------
# Cloudflare challenge detection
# ---------------------------------------------------------------------------

_CF_MARKERS = ("cf-turnstile", 'id="challenge-form"', "jschl-answer", "cf_captcha_kind")


def _is_cf_challenge(text: str) -> bool:
    """Return True if the response body looks like a Cloudflare challenge page."""
    head = text[:4000]
    return "cloudflare" in head.lower() and any(m in head for m in _CF_MARKERS)


# ---------------------------------------------------------------------------
# Proxy config (read once from env; can be overridden per-call)
# ---------------------------------------------------------------------------

def _env_proxies() -> dict[str, str]:
    """Build a proxy dict from standard env vars (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY)."""
    proxies: dict[str, str] = {}
    for var, key in (
        ("HTTPS_PROXY", "https"),
        ("HTTP_PROXY",  "http"),
        ("ALL_PROXY",   "all"),
    ):
        val = os.environ.get(var) or os.environ.get(var.lower())
        if val:
            proxies[key] = val
    return proxies


# ---------------------------------------------------------------------------
# curl_cffi — optional Chrome TLS-fingerprint spoofing
# ---------------------------------------------------------------------------

def _can_import_curl_cffi() -> bool:
    try:
        importlib.import_module("curl_cffi")
        return True
    except Exception:
        return False


def _ensure_curl_cffi() -> bool:
    """Return True if curl_cffi is usable; auto-install if missing."""
    if _can_import_curl_cffi():
        return True
    log.info("curl_cffi not found — installing automatically...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "curl-cffi"],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        log.info("curl_cffi installed successfully.")
    except Exception as e:
        log.warning("curl_cffi auto-install failed: %s", e)
        return False
    return _can_import_curl_cffi()


# Lazily resolved once per process: True = use curl_cffi, False = urllib only
_USE_CURL_CFFI: bool | None = None


def _curl_cffi_available() -> bool:
    global _USE_CURL_CFFI
    if _USE_CURL_CFFI is None:
        _USE_CURL_CFFI = _ensure_curl_cffi()
    return _USE_CURL_CFFI


# Persistent curl_cffi Session — shares cookies across fetch_url calls.
_cffi_session: object | None = None


def _get_cffi_session(proxies: dict[str, str]) -> object:
    """Return (and lazily create) the module-level curl_cffi Session."""
    global _cffi_session
    if _cffi_session is None:
        from curl_cffi import requests as cffi_requests
        _cffi_session = cffi_requests.Session()
    # Apply proxies every call in case env changed; curl_cffi ignores empty dict
    if proxies and hasattr(_cffi_session, "proxies"):
        _cffi_session.proxies = proxies  # type: ignore[union-attr]
    return _cffi_session


# ---------------------------------------------------------------------------
# urllib session — shared cookie jar + proxy + SSL
# ---------------------------------------------------------------------------

def _build_opener(proxies: dict[str, str]) -> urllib.request.OpenerDirector:
    handlers: list = [
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=make_ssl_context()),
    ]
    if proxies:
        # urllib ProxyHandler expects {"http": url, "https": url}
        ph = {k: v for k, v in proxies.items() if k in ("http", "https")}
        if ph:
            handlers.append(urllib.request.ProxyHandler(ph))
    return urllib.request.build_opener(*handlers)


_ENV_PROXIES: dict[str, str] = _env_proxies()

# Shared cookie jar / opener using env proxies (created once at import time)
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
    urllib.request.HTTPSHandler(context=make_ssl_context()),
    *(
        [urllib.request.ProxyHandler({k: v for k, v in _ENV_PROXIES.items()
                                       if k in ("http", "https")})]
        if _ENV_PROXIES else []
    ),
)


# ---------------------------------------------------------------------------
# Browser-like headers
# ---------------------------------------------------------------------------

# Accept-Encoding includes br so servers that support Brotli will use it.
# _decompress() handles br when the brotli/brotlicffi package is present.
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
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


# ---------------------------------------------------------------------------
# Decompression helpers
# ---------------------------------------------------------------------------

def _decompress(raw: bytes, encoding: str) -> bytes:
    """Decompress raw bytes according to Content-Encoding."""
    enc = encoding.lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc in ("deflate", "zlib"):
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -15)  # raw deflate (no zlib wrapper)
    if enc == "br":
        try:
            import brotli  # type: ignore[import]
            return brotli.decompress(raw)
        except ImportError:
            pass
        try:
            import brotlicffi  # type: ignore[import]
            return brotlicffi.decompress(raw)
        except ImportError:
            pass
        # No brotli library available — return raw bytes; caller decodes with errors="replace"
        return raw
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
        netloc = parts.netloc  # IPv6 literal — keep as-is
    path     = urllib.parse.quote(parts.path    or "", safe="/%:@!$&'()*+,;=-._~")
    query    = urllib.parse.quote(parts.query   or "", safe="=&%:@!$'()*+,;/?-._~")
    fragment = urllib.parse.quote(parts.fragment or "", safe="%:@!$&'()*+,;=/?-._~")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, fragment))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    name="fetch_url",
    description=(
        "Fetch the content of a URL and return the response body as text. "
        "Uses Chrome TLS fingerprint (curl_cffi) when available, falling back to urllib. "
        "Browser-like headers, shared cookie jar, gzip/deflate/brotli decompression, "
        "and exponential backoff on 429/503. "
        "Proxy via HTTP_PROXY / HTTPS_PROXY env vars or the proxies parameter. "
        "Blocks requests to private/internal IPs (SSRF protection). "
        "For JS-rendered pages or Cloudflare interstitials use browser_navigate. "
        "For clean LLM-friendly markdown use jina_read."
    ),
    parallel_safe=True,
)
def fetch_url(
    url: str,
    max_bytes: int = 65536,
    timeout: int = 20,
    proxies: dict | None = None,
) -> ToolResult:
    """Fetch a URL with SSRF protection, TLS fingerprint spoofing, and CF challenge detection."""
    if not url.startswith(("http://", "https://")):
        return ToolResult.error(f"Invalid URL (must start with http/https): {url}")
    url = _normalize_url(url)

    # SSRF gate — must pass before any socket is opened
    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ToolResult.error(ssrf_err)

    effective_proxies = proxies if proxies is not None else _ENV_PROXIES

    # --- curl_cffi path: Chrome TLS fingerprint + h2 negotiation ---
    if _curl_cffi_available():
        session = _get_cffi_session(effective_proxies)
        for attempt in range(3):
            try:
                resp = session.get(  # type: ignore[attr-defined]
                    url,
                    impersonate="chrome124",
                    headers={k: v for k, v in _BROWSER_HEADERS.items()
                              if k != "Accept-Encoding"},  # curl_cffi sets this itself
                    timeout=timeout,
                    max_redirects=10,
                )
                resp.raise_for_status()
                text = resp.text
                if _is_cf_challenge(text):
                    return ToolResult.error(
                        "Cloudflare challenge page detected — try jina_read or browser_navigate"
                    )
                if len(text) > max_bytes:
                    text = f"[Content truncated at {max_bytes} bytes]\n" + text[:max_bytes]
                return ToolResult.ok(text)
            except Exception as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (429, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                log.debug("curl_cffi fetch failed (%s), falling back to urllib: %s", code, e)
                break

    # --- urllib fallback ---
    opener = _opener if effective_proxies == _ENV_PROXIES else _build_opener(effective_proxies)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=dict(_BROWSER_HEADERS))
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                enc = (resp.headers.get("Content-Encoding") or "").strip()
                if enc:
                    try:
                        raw = _decompress(raw, enc)
                    except Exception:
                        pass
                truncated = len(raw) > max_bytes
                if truncated:
                    raw = raw[:max_bytes]
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                if _is_cf_challenge(text):
                    return ToolResult.error(
                        "Cloudflare challenge page detected — try jina_read or browser_navigate"
                    )
                if truncated:
                    text = f"[Content truncated at {max_bytes} bytes]\n{text}"
                return ToolResult.ok(text)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 ** attempt)
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
    parallel_safe=True,
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
