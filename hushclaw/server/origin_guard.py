"""Browser-origin and Host checks for the local server.

The server binds to loopback by default and historically trusted every
connection. Browsers let any web page open a WebSocket or send a CORS request
to ``127.0.0.1``, so without these checks a page the user merely visits could
drive the agent. DNS rebinding can also point an attacker hostname at
loopback, which is why the Host header is checked as well.

Rules:
- A request without an ``Origin`` header is not from a browser page and is
  allowed (CLI tools, curl, native clients). They already run as the user.
- Browser origins must be loopback (any port), the configured
  ``public_base_url`` origin, or listed in ``server.allowed_origins``.
- When bound to loopback, the ``Host`` header must be a loopback name or a
  host from the configured allowlist.
"""
from __future__ import annotations

import hmac
from urllib.parse import urlparse

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_WILDCARD_BIND = frozenset({"", "0.0.0.0", "::"})


def _normalize_host(host: str) -> str:
    return str(host or "").strip().strip("[]").lower().rstrip(".")


def _host_without_port(host_header: str) -> str:
    value = str(host_header or "").strip()
    if value.startswith("["):
        end = value.find("]")
        return _normalize_host(value[1:end] if end > 0 else value)
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return _normalize_host(value)


def _origin_key(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme in {"https", "wss"} else 80
    return f"{parsed.scheme.lower()}://{_normalize_host(parsed.hostname)}:{port}"


def is_loopback_bind(host: str) -> bool:
    return _normalize_host(host) in _LOOPBACK_HOSTS or _normalize_host(host).startswith("127.")


def _extra_origins(config) -> list[str]:
    origins = [str(o) for o in (getattr(config, "allowed_origins", None) or []) if str(o).strip()]
    public = str(getattr(config, "public_base_url", "") or "").strip()
    if public:
        origins.append(public)
    return origins


def origin_allowed(origin: str, config) -> bool:
    """Return True when a browser Origin may talk to this server."""
    value = str(origin or "").strip()
    if not value:
        return True
    if value.lower() == "null":
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = _normalize_host(parsed.hostname)
    if hostname in _LOOPBACK_HOSTS or hostname.startswith("127."):
        return True
    key = _origin_key(value)
    return any(key == _origin_key(extra) for extra in _extra_origins(config))


def host_allowed(host_header: str, config) -> bool:
    """Return True when the Host header is acceptable (DNS-rebinding guard)."""
    bind = str(getattr(config, "host", "") or "")
    if not is_loopback_bind(bind):
        # The owner exposed the server beyond loopback on purpose; clients may
        # reach it through any LAN name. Origin checks and api_key still apply.
        return True
    host = _host_without_port(host_header)
    if not host:
        # HTTP/1.0 clients without Host are not browsers.
        return True
    if host in _LOOPBACK_HOSTS or host.startswith("127."):
        return True
    for extra in _extra_origins(config):
        parsed = urlparse(extra)
        if parsed.hostname and _normalize_host(parsed.hostname) == host:
            return True
    return False


def request_allowed(headers, config) -> bool:
    """Combined Origin + Host check for a header mapping (case-insensitive get)."""
    origin = _get_header(headers, "Origin")
    host = _get_header(headers, "Host")
    return origin_allowed(origin, config) and host_allowed(host, config)


def _get_header(headers, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return str(value or "")
    except Exception:
        return ""


def keys_match(provided: str, expected: str) -> bool:
    """Constant-time API key comparison."""
    if not expected:
        return False
    return hmac.compare_digest(str(provided or "").encode(), str(expected).encode())


def warn_if_exposed(config, log) -> None:
    bind = str(getattr(config, "host", "") or "")
    if not is_loopback_bind(bind) and not getattr(config, "api_key", ""):
        log.warning(
            "Server is bound to %r without server.api_key: anyone on the network can "
            "control this agent. Set server.api_key or bind to 127.0.0.1.",
            bind or "0.0.0.0",
        )
        print(
            "WARNING: HushClaw is listening beyond localhost without an API key. "
            "Set server.api_key or bind to 127.0.0.1."
        )
