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


def ca_bundle_path() -> str | None:
    """Return a CA bundle file path suitable for the *requests* library.

    The lark-oapi SDK (and other requests-based code) honours the
    ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE`` env vars but does NOT use the
    ssl.SSLContext returned by :func:`make_ssl_context`.  Call this to get a
    plain file path that can be passed to ``requests.get(verify=path)`` or set
    as an env var before starting a requests-based SDK.

    System CA paths are tried **before** certifi because on managed macOS
    machines the corporate root CA is added to the Keychain (and therefore
    exported to /etc/ssl/cert.pem) but is absent from certifi's curated
    bundle.  certifi is used as a last-resort fallback only.

    Returns ``None`` when no suitable bundle is found.
    """
    # 1. System CA bundles — include IT-managed / corporate root CAs
    for path in (
        "/etc/ssl/cert.pem",                    # macOS (Keychain export), FreeBSD
        "/etc/ssl/certs/ca-certificates.crt",   # Debian / Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL / CentOS
        "/etc/ssl/ca-bundle.pem",               # OpenSUSE
    ):
        if os.path.exists(path):
            return path

    # 2. certifi — widely available but lacks corporate / private CAs
    try:
        import certifi
        return certifi.where()
    except ImportError:
        pass

    return None
