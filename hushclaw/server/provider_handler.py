"""Provider connectivity test handler — extracted from server.py.

Responsible for the ``test_provider`` WebSocket message:
DNS → TCP → TLS → API reachability → Authentication → Model list.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("hushclaw.server.provider")


async def handle_test_provider(ws, data: dict, gateway) -> None:
    """Handle a ``test_provider`` WS message.

    Streams ``test_provider_step`` / ``test_provider_result`` events back over *ws*.
    All errors are caught — the caller never needs to handle exceptions from this.
    """
    loop = asyncio.get_event_loop()

    async def step(step_id: str, status: str, label: str, detail: str = "") -> None:
        await ws.send(json.dumps({
            "type": "test_provider_step",
            "step": step_id, "status": status,
            "label": label, "detail": detail,
        }))

    async def finish(ok: bool, detail: str = "") -> None:
        await ws.send(json.dumps({"type": "test_provider_result", "ok": ok, "detail": detail}))

    try:
        await _run_test_provider(ws, data, gateway, step, finish, loop)
    except Exception as e:
        log.exception("Unexpected error in test_provider")
        try:
            await finish(False, f"Unexpected error: {e}")
        except Exception:
            pass


async def _run_test_provider(ws, data: dict, gateway, step, finish, loop) -> None:
    import socket
    import ssl
    import time
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse

    base_cfg = gateway.base_agent.config.provider
    base_url  = (data.get("base_url") or base_cfg.base_url or "").strip().rstrip("/")
    api_key   = (data.get("api_key")  or base_cfg.api_key  or "").strip()
    provider_name = (data.get("provider") or base_cfg.name or "").strip()
    model     = (data.get("model") or gateway.base_agent.config.agent.model or "").strip()
    # TEX Router uses qualified IDs (azure/...); config may still hold a bare or Claude id.
    if provider_name in ("transsion", "tex"):
        if not model or "/" not in model:
            model = "azure/gpt-4o-mini"
            log.info("test_provider: normalized transsion probe model to %s", model)

    if not base_url:
        await finish(False, "Base URL is empty.")
        return

    parsed   = urlparse(base_url)
    host     = parsed.hostname or ""
    port     = parsed.port or (443 if parsed.scheme == "https" else 80)
    is_https = parsed.scheme == "https"

    if not host:
        await finish(False, f"Cannot parse host from URL: {base_url}")
        return

    # ── Step 1: DNS resolution ────────────────────────────────────────────
    await step("dns", "running", "DNS Resolution", f"Resolving {host}…")
    try:
        t0 = time.monotonic()
        addrs = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            ),
            timeout=10,
        )
        if not addrs:
            raise socket.gaierror(f"No addresses returned for {host}")
        ip = addrs[0][4][0]
        ms = int((time.monotonic() - t0) * 1000)
        await step("dns", "ok", "DNS Resolution", f"{host} → {ip}  ({ms} ms)")
    except (asyncio.TimeoutError, TimeoutError):
        await step("dns", "error", "DNS Resolution",
                   f"DNS lookup timed out for '{host}'. Check hostname / network.")
        await finish(False, "DNS resolution timed out.")
        return
    except OSError as e:
        await step("dns", "error", "DNS Resolution",
                   f"Cannot resolve '{host}': {e}. Check hostname / network / VPN.")
        await finish(False, "DNS resolution failed.")
        return

    # ── Step 2: TCP connectivity ──────────────────────────────────────────
    await step("tcp", "running", "TCP Connect", f"Connecting to {ip}:{port}…")
    try:
        t0 = time.monotonic()
        def _tcp():
            s = socket.create_connection((host, port), timeout=6)
            s.close()
        await asyncio.wait_for(loop.run_in_executor(None, _tcp), timeout=8)
        ms = int((time.monotonic() - t0) * 1000)
        await step("tcp", "ok", "TCP Connect", f"Connected  ({ms} ms)")
    except (asyncio.TimeoutError, socket.timeout):
        await step("tcp", "error", "TCP Connect",
                   f"Timed out connecting to {host}:{port}. Firewall or wrong port?")
        await finish(False, "TCP connection timed out.")
        return
    except ConnectionRefusedError:
        await step("tcp", "error", "TCP Connect",
                   f"Connection refused on port {port}. Service may be down.")
        await finish(False, "Connection refused.")
        return
    except OSError as e:
        await step("tcp", "error", "TCP Connect", f"Network error: {e}")
        await finish(False, "TCP connection failed.")
        return

    # ── Step 3: TLS / certificate ─────────────────────────────────────────
    if is_https:
        await step("tls", "running", "TLS Handshake", "Verifying SSL certificate…")
        try:
            def _tls():
                ctx = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=6) as raw:
                    with ctx.wrap_socket(raw, server_hostname=host) as ssock:
                        return ssock.getpeercert()
            cert = await asyncio.wait_for(loop.run_in_executor(None, _tls), timeout=8)
            expiry = cert.get("notAfter", "unknown")
            await step("tls", "ok", "TLS Handshake", f"Certificate valid · expires {expiry}")
        except ssl.SSLCertVerificationError as e:
            await step("tls", "warn", "TLS Handshake",
                       f"Certificate verification failed (self-signed?): {e}. Proceeding…")
        except ssl.SSLError as e:
            await step("tls", "error", "TLS Handshake", f"TLS error: {e}")
            await finish(False, "TLS handshake failed.")
            return
        except Exception as e:
            await step("tls", "warn", "TLS Handshake", f"Could not inspect certificate: {e}")
    else:
        await step("tls", "skip", "TLS Handshake", "HTTP (plain) — skipped")

    # ── Step 4: API endpoint reachability ─────────────────────────────────
    await step("api", "running", "API Endpoint", f"Probing {base_url}…")
    try:
        def _http_probe():
            req = urllib.request.Request(base_url, method="GET",
                                         headers={"User-Agent": "HushClaw/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    return r.status, ""
            except urllib.error.HTTPError as e:
                return e.code, e.reason
        status, reason = await asyncio.wait_for(
            loop.run_in_executor(None, _http_probe), timeout=10
        )
        if status < 500:
            await step("api", "ok", "API Endpoint", f"HTTP {status} — endpoint reachable")
        else:
            await step("api", "warn", "API Endpoint",
                       f"HTTP {status} {reason} — server error, but host is up")
    except Exception as e:
        await step("api", "warn", "API Endpoint",
                   f"Probe returned unexpected result: {e}. Continuing anyway…")

    # ── Step 5: Authentication & model list ───────────────────────────────
    if not api_key and provider_name != "ollama":
        await step("auth", "skip", "API Authentication", "No API key provided — skipped")
        await finish(True, "Network checks passed. Enter an API key to validate authentication.")
        return

    await step("auth", "running", "API Authentication", "Verifying API key…")
    try:
        from hushclaw.config.schema import ProviderConfig
        from hushclaw.providers.registry import get_provider
        from hushclaw.providers.base import Message as LLMMessage

        cfg = ProviderConfig(
            name=provider_name,
            api_key=api_key,
            base_url=base_url,
            timeout=10,
            max_retries=0,
        )
        provider = get_provider(cfg)
        list_timeout = 25.0 if provider_name in ("transsion", "tex") else 10.0
        log.info(
            "test_provider: list_models provider=%s timeout=%ss",
            provider_name,
            list_timeout,
        )
        models = await asyncio.wait_for(provider.list_models(), timeout=list_timeout)

        if models:
            await step("auth", "ok", "API Authentication",
                       f"Authenticated · {len(models)} model(s) available")
        else:
            # list_models not implemented — try a 1-token completion
            log.info("test_provider: list_models empty, chat probe model=%r", model)
            await provider.complete(
                messages=[LLMMessage(role="user", content="hi")],
                system="", max_tokens=1, model=model or None,
            )
            await step("auth", "ok", "API Authentication", "Authenticated")
            models = []

    except asyncio.TimeoutError:
        await step("auth", "error", "API Authentication",
                   "Auth request timed out (10 s). API may be overloaded.")
        await finish(False, "Authentication timed out.")
        return
    except Exception as e:
        err = str(e)
        low = err.lower()
        if "401" in err or "unauthorized" in low:
            await step("auth", "error", "API Authentication",
                       "Invalid or expired API key (401). Double-check your key.")
        elif "403" in err:
            await step("auth", "error", "API Authentication",
                       "Access denied (403). Key may lack required permissions.")
        elif "429" in err or "rate" in low:
            await step("auth", "warn", "API Authentication",
                       "Rate-limited (429) — key is valid but quota exceeded.")
            await finish(True, "All checks passed (rate-limited but key accepted).")
            return
        elif "404" in err:
            await step("auth", "error", "API Authentication",
                       "Endpoint not found (404). Is the Base URL correct?")
        else:
            await step("auth", "error", "API Authentication", f"Auth failed: {err[:200]}")
        await finish(False, "Authentication failed.")
        return

    # ── Step 6: Model availability ────────────────────────────────────────
    def _model_id(m) -> str:
        return m.get("id", "") if isinstance(m, dict) else str(m)

    if model and models:
        if any(_model_id(m) == model for m in models):
            await step("model", "ok", "Model Check", f"'{model}' is available")
        else:
            ids = [_model_id(m) for m in models[:5]]
            await step("model", "warn", "Model Check",
                       f"'{model}' not found in model list. Available: {', '.join(ids)}…")
    else:
        await step("model", "skip", "Model Check",
                   "Skipped (model list unavailable or no model specified)")

    await finish(True, "All checks passed.")
