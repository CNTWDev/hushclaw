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
from hushclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall, _with_retry
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


def _tool_to_responses_schema(tool: dict) -> dict:
    name = str(tool.get("name", "")).strip()
    if not name:
        return {}
    params = tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    params = dict(params)
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return {
        "type": "function",
        "name": name,
        "description": tool.get("description", ""),
        "parameters": params,
    }


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                parts.append(str(block.get("text", "")))
        if parts:
            return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


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


def _to_responses_input(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-style messages to Responses API input items."""
    items: list[dict] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": _message_text(msg.get("content", "")),
            })
            continue

        if role not in ("user", "assistant", "system"):
            role = "user"

        items.append({
            "role": role,
            "content": _message_text(msg.get("content", "")),
        })
    return items


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
        "input": _to_responses_input(messages),
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
            parsed = json.loads(resp.read())
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


def _image_to_openai_block(data_uri_or_url: str) -> dict:
    """Convert a data URI or HTTPS URL to an OpenAI image_url content block."""
    return {
        "type": "image_url",
        "image_url": {"url": data_uri_or_url, "detail": "auto"},
    }


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content if isinstance(m.content, str) else json.dumps(m.content),
            })
        elif isinstance(m.content, list):
            # Anthropic-style content blocks → convert to OpenAI chat-completions format:
            # extract text into content, tool_use blocks into tool_calls array.
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    if block.get("text"):
                        text_parts.append(block["text"])
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            # Never use content=null — Gemini/Vertex OpenAI proxies map that to zero
            # ``parts`` and return 400 ("must include at least one parts field").
            text_flat = "\n".join(text_parts)
            msg = {"role": m.role, "content": text_flat if text_flat else ""}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            result.append(msg)
        else:
            # Plain text message — inject image blocks if present
            if m.images:
                content_parts: list[dict] = [_image_to_openai_block(img) for img in m.images]
                if m.content:
                    content_parts.append({"type": "text", "text": m.content})
                result.append({"role": m.role, "content": content_parts})
            else:
                result.append({"role": m.role, "content": m.content})
    return result


def _normalize_messages_for_gemini_openai_proxy(
    messages: list[dict],
    *,
    model: str = "",
    label: str = "",
) -> None:
    """In-place fix for gateways (e.g. TEX → Vertex) that translate Chat Completions
    to Gemini ``Content.parts``.  Null or empty user text becomes zero parts → 400.
    """
    for msg in messages:
        role = msg.get("role") or ""
        c = msg.get("content")
        if c is None:
            msg["content"] = ""
            c = ""
        if isinstance(c, str) and c == "" and role == "user":
            msg["content"] = " "
        elif isinstance(c, list):
            if len(c) == 0 and role == "user":
                msg["content"] = " "

    # TEX routes google/gemini-* to a backend that accepts Gemini roles only:
    # role must be `user` | `model` (not system/assistant/tool).
    model_l = (model or "").lower()
    if label == "transsion" and ("gemini" in model_l or model_l.startswith("google/")):
        sys_chunks: list[str] = []
        normalized: list[dict] = []
        for msg in messages:
            role = str(msg.get("role") or "")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    sys_chunks.append(content.strip())
                continue

            # Normalize role set to Gemini-compatible values.
            if role == "assistant":
                msg["role"] = "model"
            elif role == "tool":
                txt = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                msg = {"role": "user", "content": f"[tool_result]\n{txt}" if txt else "[tool_result]"}
            elif role not in ("user", "model"):
                msg["role"] = "user"

            c2 = msg.get("content")
            if c2 is None:
                c2 = ""
            if isinstance(c2, str):
                msg["content"] = c2 if c2 else " "
            elif isinstance(c2, list) and len(c2) == 0:
                msg["content"] = " "
            normalized.append(msg)

        if sys_chunks:
            sys_text = "[system]\n" + "\n\n".join(sys_chunks)
            if normalized and normalized[0].get("role") == "user" and isinstance(normalized[0].get("content"), str):
                u = normalized[0]["content"] or " "
                normalized[0]["content"] = f"{sys_text}\n\n{u}"
            else:
                normalized.insert(0, {"role": "user", "content": sys_text})
        messages[:] = normalized


def _sanitize_openai_messages_for_chat(messages: list[dict]) -> None:
    """Sanitize assistant tool_calls for strict OpenAI-compatible gateways."""
    for msg in messages:
        if str(msg.get("role") or "") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        sanitized_calls: list[dict] = []
        for i, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name", "")).strip()
            if not name:
                continue
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, str):
                args = raw_args.strip() or "{}"
            else:
                try:
                    args = json.dumps(raw_args if raw_args is not None else {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
            try:
                json.loads(args)
            except Exception:
                args = "{}"
            sanitized_calls.append({
                "id": str(tc.get("id", "") or f"call_{i}"),
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
        if sanitized_calls:
            msg["tool_calls"] = sanitized_calls
        else:
            msg.pop("tool_calls", None)


def _parse_response_payload(data: dict) -> tuple[str, list[ToolCall], int, int]:
    """Parse both chat-completions and responses payloads."""
    if "choices" in data:
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content_text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                inp = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), input=inp))
        usage = data.get("usage", {})
        return (
            content_text,
            tool_calls,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    content_text = data.get("output_text") or ""
    tool_calls = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            try:
                inp = json.loads(item.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {}
            call_id = item.get("call_id") or item.get("id", "")
            tool_calls.append(ToolCall(id=call_id, name=item.get("name", ""), input=inp))
        elif not content_text and item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text") and block.get("text"):
                    content_text += str(block.get("text"))

    usage = data.get("usage", {})
    in_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
    return content_text, tool_calls, in_tok, out_tok


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
        api_messages.extend(_to_openai_messages(messages))
        _normalize_messages_for_gemini_openai_proxy(
            api_messages,
            model=model,
            label=self._label,
        )
        _sanitize_openai_messages_for_chat(api_messages)

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
            content_text, tool_calls, in_tok, out_tok = _parse_response_payload(data)
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
