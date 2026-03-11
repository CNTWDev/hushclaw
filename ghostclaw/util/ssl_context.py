"""Shared SSL context helper for urllib-based providers."""
from __future__ import annotations

import os
import ssl

_SSL_CONTEXT: ssl.SSLContext | None = None


def make_ssl_context() -> ssl.SSLContext:
    """Return a cached SSL context with verified certificates.

    Tries certifi first, then known system CA bundle paths (covers macOS
    python.org installs where the default context has no roots loaded),
    then falls back to ssl.create_default_context().
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT

    # 1. certifi (installed alongside many packages)
    try:
        import certifi
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        return _SSL_CONTEXT
    except ImportError:
        pass

    # 2. Known system CA bundle locations
    for path in (
        "/etc/ssl/cert.pem",                    # macOS, FreeBSD
        "/etc/ssl/certs/ca-certificates.crt",   # Debian / Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL / CentOS
        "/etc/ssl/ca-bundle.pem",               # OpenSUSE
    ):
        if os.path.exists(path):
            _SSL_CONTEXT = ssl.create_default_context(cafile=path)
            return _SSL_CONTEXT

    # 3. Default (may fail on python.org macOS installs without cert fix)
    _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT
