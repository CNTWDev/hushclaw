"""OpenAI-compatible provider — urllib, no SDK."""
from __future__ import annotations

import asyncio
import functools
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urlunparse

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider, LLMResponse, Message, _with_retry
from hushclaw.providers.openai_transforms import (
    normalize_messages_for_gemini_openai_proxy,
    parse_response_payload,
    sanitize_openai_messages_for_chat,
    to_openai_messages,
    to_responses_input,
    _tool_to_responses_schema,
)
from hushclaw.util.ssl_context import make_ssl_context
from hushclaw.util.logging import get_logger

log = get_logger("providers.openai_raw")

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hushclaw-openai")


def _normalize_base_url(base_url: str) -> str:
    """Normalize OpenAI-compatible base URLs to include /v1 when omitted."""
    url = (base_url or "https://api.openai.com/v1").rstrip("/")
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path in ("", "/"):
        parsed = parsed._replace(path="/v1")
        return urlunparse(parsed).rstrip("/")
    return url


def _mask_key(api_key: str) -> str:
    if not api_key:
        return "<empty>"
    if len(api_key) <= 8:
        return "set"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _format_http_error(
    code: int, body: str, base_url: str, api_key: str, label: str = "openai-raw"
) -> str:
    """Build a user-facing provider error with actionable hints."""
    try:
        payload = json.loads(body)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        # Handle both flat {"message": ...} and nested {"error": {"message": ...}} formats
        err_obj = payload.get("error") if isinstance(payload.get("error"), dict) else payload
        err_code = str(err_obj.get("code", "") or payload.get("code", "")).upper()
        msg = str(err_obj.get("message", "")).strip()
        if code == 401 and err_code == "INVALID_API_KEY":
            return (
                f"{label} API auth failed (INVALID_API_KEY). "
                f"base_url={base_url}, api_key={_mask_key(api_key)}. "
                "Check that this key matches the platform you're connecting to."
            )
        if msg:
            return f"{label} API error {code}: {msg}" + (f" (code={err_code})" if err_code else "")

    return f"{label} API error {code}: {body}"


def _log_openai_response_summary(label: str, endpoint: str, data: dict) -> None:
    """Log a concise response shape summary for diagnostics."""
    try:
        if "choices" in data:
            choice = (data.get("choices") or [{}])[0] or {}
            msg = choice.get("message", {}) or {}
            content = msg.get("content")
            tool_calls = msg.get("tool_calls") or []
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            log.info(
                "[%s] %s response: finish=%s content_type=%s content_len=%s tool_calls=%d "
                "usage_keys=%s prompt_tokens=%s completion_tokens=%s",
                label,
                endpoint,
                choice.get("finish_reason"),
                type(content).__name__ if content is not None else "None",
                len(content) if isinstance(content, str) else -1,
                len(tool_calls),
                sorted(list(usage.keys())) if usage else [],
                usage.get("prompt_tokens") if usage else None,
                usage.get("completion_tokens") if usage else None,
            )
            return

        output = data.get("output") or []
        output_types = [item.get("type") for item in output if isinstance(item, dict)]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        log.info(
            "[%s] %s response: output_items=%d output_types=%s output_text_len=%d "
            "usage_keys=%s input_tokens=%s output_tokens=%s",
            label,
            endpoint,
            len(output),
            output_types[:8],
            len(data.get("output_text") or ""),
            sorted(list(usage.keys())) if usage else [],
            usage.get("input_tokens") if usage else usage.get("prompt_tokens"),
            usage.get("output_tokens") if usage else usage.get("completion_tokens"),
        )
    except Exception as e:
        log.warning("[%s] failed to summarize %s response: %s", label, endpoint, e)


def _sync_request_responses(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    timeout: int,
    instructions: str = "",
) -> dict:
    payload: dict = {
        "model": model,
        "input": to_responses_input(messages),
        "max_output_tokens": max_tokens,
    }
    if instructions:
        instr_str = "\n".join(str(s) for s in instructions if s) if isinstance(instructions, (list, tuple)) else str(instructions)
        payload["instructions"] = instr_str
    if tools:
        sanitized_tools = [_tool_to_responses_schema(t) for t in tools]
        sanitized_tools = [t for t in sanitized_tools if t.get("name")]
        if sanitized_tools:
            payload["tools"] = sanitized_tools
            payload["tool_choice"] = "auto"

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "OpenAI/Python 1.56.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            parsed = json.loads(resp.read())
            _log_openai_response_summary(label, "/responses", parsed)
            return parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ProviderError(_format_http_error(e.code, body, base_url, api_key)) from e
    except Exception as e:
        raise ProviderError(f"Request failed: {e}") from e


def _sync_request(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    timeout: int,
    label: str = "openai-raw",
) -> dict:
    url = f"{base_url}/chat/completions"
    log.debug(
        "[%s] POST %s  model=%s  key=%s  messages=%d  tools=%d",
        label, url, model, _mask_key(api_key), len(messages), len(tools) if tools else 0,
    )

    def _build_payload(token_key: str) -> dict:
        p: dict = {
            "model": model,
            token_key: max_tokens,
            "messages": messages,
        }
        if tools:
            sanitized_tools = []
            for t in tools:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name", "")).strip()
                if not name:
                    continue
                params = t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
                if not isinstance(params, dict):
                    params = {"type": "object", "properties": {}}
                params = dict(params)
                params.setdefault("type", "object")
                params.setdefault("properties", {})
                sanitized_tools.append({
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": params,
                })
            if sanitized_tools:
                p["tools"] = [{"type": "function", "function": t} for t in sanitized_tools]
                p["tool_choice"] = "auto"
        return p

    def _do_post(payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "OpenAI/Python 1.56.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            log.debug("[%s] %s → HTTP 200", label, url)
            raw_bytes = resp.read()
            parsed = json.loads(raw_bytes)
            # Detect gateways that wrap errors in HTTP-200 bodies (e.g. Transsion TEX Router).
            # Pattern: {"metadata": {"code": 4xx/5xx, "debugMessage": "..."}}
            if isinstance(parsed, dict) and not parsed.get("choices") and not parsed.get("output") and not parsed.get("output_text"):
                meta = parsed.get("metadata")
                if isinstance(meta, dict):
                    meta_code = int(meta.get("code") or 0)
                    if meta_code >= 400:
                        debug_msg = str(meta.get("debugMessage") or meta.get("message") or "")
                        raise ProviderError(f"{label} gateway error {meta_code}: {debug_msg}")
            _log_openai_response_summary(label, "/chat/completions", parsed)
            return parsed

    token_key = "max_completion_tokens" if "gpt-5" in (model or "").lower() else "max_tokens"
    payload = _build_payload(token_key)
    try:
        return _do_post(payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error(
            "[%s] %s → HTTP %d  key=%s  body=%s",
            label, url, e.code, _mask_key(api_key), body[:500],
        )
        # Some gateways (e.g. TEX for azure/gpt-5.*) require max_completion_tokens.
        if (
            token_key == "max_tokens"
            and e.code == 400
            and "unsupported parameter" in body.lower()
            and "max_tokens" in body
            and "max_completion_tokens" in body
        ):
            try:
                log.info("[%s] retrying with max_completion_tokens for model=%s", label, model)
                return _do_post(_build_payload("max_completion_tokens"))
            except urllib.error.HTTPError as e_retry:
                body_retry = e_retry.read().decode("utf-8", errors="replace")
                raise ProviderError(
                    _format_http_error(e_retry.code, body_retry, base_url, api_key, label)
                ) from e_retry
        if e.code in (400, 404) and ("/v1/responses" in body or "responses" in body.lower() and "legacy" in body.lower()):
            try:
                sys_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
                non_sys = [m for m in messages if m.get("role") != "system"]
                return _sync_request_responses(
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    messages=non_sys,
                    tools=tools,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    instructions="\n".join(sys_parts),
                )
            except urllib.error.HTTPError as e2:
                body2 = e2.read().decode("utf-8", errors="replace")
                raise ProviderError(_format_http_error(e2.code, body2, base_url, api_key, label)) from e2
        raise ProviderError(_format_http_error(e.code, body, base_url, api_key, label)) from e
    except Exception as e:
        log.error("[%s] %s → exception: %s", label, url, e)
        raise ProviderError(f"Request failed: {e}") from e


def _sync_list_models(
    api_key: str, base_url: str, timeout: int, label: str = "openai-raw"
) -> list[str]:
    """GET {base_url}/models — raises ProviderError on auth / server errors.

    Historically this swallowed all exceptions and returned [], which made callers
    think listing succeeded and fall through to a probe completion — often with a
    wrong default model (e.g. TEX Router needs ``azure/gpt-4o-mini``).
    """
    base = (base_url or "").rstrip("/")
    url = f"{base}/models"
    log.info("[%s] list_models GET %s timeout=%ss", label, url, timeout)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "HushClaw/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            raw = resp.read()
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.warning(
            "[%s] list_models → HTTP %s body (truncated)=%s",
            label,
            e.code,
            body[:800],
        )
        if e.code in (401, 403):
            raise ProviderError(
                _format_http_error(e.code, body, base_url, api_key, label)
            ) from e
        if e.code == 404:
            log.info("[%s] list_models 404 — empty list (no /models on this host)", label)
            return []
        raise ProviderError(
            _format_http_error(e.code, body, base_url, api_key, label)
        ) from e
    except Exception as e:
        log.warning("[%s] list_models failed: %s", label, e)
        raise ProviderError(f"{label} list_models failed: {e}") from e

    if not isinstance(data, dict):
        raise ProviderError(f"{label} list_models: expected JSON object, got {type(data).__name__}")

    items = data.get("data")
    if items is None:
        items = data.get("models", [])
    if not isinstance(items, list):
        log.warning(
            "[%s] list_models: unexpected payload keys=%s",
            label,
            list(data.keys()),
        )
        return []

    ids: list[str] = []
    for m in items:
        if isinstance(m, dict) and m.get("id"):
            ids.append(str(m["id"]))
    ids.sort()
    log.info("[%s] list_models → %d id(s)", label, len(ids))
    return ids


class OpenAIRawProvider(LLMProvider):
    """OpenAI-compatible provider using urllib.

    provider_label: friendly name used in logs/errors (default "openai-raw").
    Set to "minimax", "groq", etc. when instantiated via the registry so logs
    and error messages reflect the actual service being called.
    """

    name = "openai-raw"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        provider_label: str = "openai-raw",
    ) -> None:
        self._label = provider_label
        # Do NOT fall back to OPENAI_API_KEY here — the config loader already maps
        # provider-specific env vars (MINIMAX_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY…)
        # to provider.api_key before instantiation.
        self.api_key = api_key.strip()
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        if not self.api_key:
            raise ProviderError(
                f"{self._label} API key not found. Configure provider.api_key in "
                "hushclaw.toml or run `hushclaw serve` and use the Settings wizard."
            )
        log.info(
            "[%s] provider init: base_url=%s  key=%s",
            self._label, self.base_url, _mask_key(self.api_key),
        )

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        model = model or "gpt-4o-mini"
        api_messages = []
        if system:
            # system may be a (stable, dynamic) tuple from ContextEngine — flatten to str
            if isinstance(system, (list, tuple)):
                system_str = "\n\n".join(str(s) for s in system if s)
            else:
                system_str = str(system)
            # Skip empty system — Vertex adapters may emit invalid empty parts.
            if system_str.strip():
                api_messages.append({"role": "system", "content": system_str})
        api_messages.extend(to_openai_messages(messages))
        normalize_messages_for_gemini_openai_proxy(
            api_messages,
            model=model,
            label=self._label,
        )
        sanitize_openai_messages_for_chat(api_messages)

        loop = asyncio.get_event_loop()

        async def _do():
            data = await loop.run_in_executor(
                _EXECUTOR,
                functools.partial(
                    _sync_request,
                    self.api_key, self.base_url, model, api_messages, tools, max_tokens,
                    self.timeout, self._label,
                ),
            )
            content_text, tool_calls, in_tok, out_tok = parse_response_payload(data)
            stop_reason = "tool_use" if tool_calls else "end_turn"
            return LLMResponse(
                content=content_text,
                stop_reason=stop_reason,
                tool_calls=tool_calls,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        return await _with_retry(_do, self.max_retries, self.retry_base_delay)

    async def list_models(self) -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _EXECUTOR,
            functools.partial(
                _sync_list_models,
                self.api_key,
                self.base_url,
                self.timeout,
                self._label,
            ),
        )
