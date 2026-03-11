"""OpenAI-compatible provider — urllib, no SDK."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urlunparse

from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall, _with_retry
from ghostclaw.util.ssl_context import make_ssl_context


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ghostclaw-openai")


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


def _format_http_error(code: int, body: str, base_url: str, api_key: str) -> str:
    """Build a user-facing provider error with actionable hints."""
    try:
        payload = json.loads(body)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        err_code = str(payload.get("code", "")).upper()
        msg = str(payload.get("message", "")).strip()
        if code == 401 and err_code == "INVALID_API_KEY":
            return (
                "OpenAI API auth failed (INVALID_API_KEY). "
                f"base_url={base_url}, api_key={_mask_key(api_key)}. "
                "Check that this key belongs to the same OpenAI-compatible platform "
                "and isn't overridden by OPENAI_API_KEY env var."
            )
        if msg:
            return f"OpenAI API error {code}: {msg} (code={err_code or 'unknown'})"

    return f"OpenAI API error {code}: {body}"


def _tool_to_responses_schema(tool: dict) -> dict:
    return {
        "type": "function",
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "parameters": tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}},
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
) -> dict:
    payload: dict = {
        "model": model,
        "input": _to_responses_input(messages),
        "max_output_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = [_tool_to_responses_schema(t) for t in tools]
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
    with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
        return json.loads(resp.read())


def _sync_request(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    timeout: int,
) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if tools:
        payload["tools"] = [{"type": "function", "function": t} for t in tools]
        payload["tool_choice"] = "auto"

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
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
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 400 and "/v1/responses" in body:
            try:
                return _sync_request_responses(
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except urllib.error.HTTPError as e2:
                body2 = e2.read().decode("utf-8", errors="replace")
                raise ProviderError(_format_http_error(e2.code, body2, base_url, api_key)) from e2
        raise ProviderError(_format_http_error(e.code, body, base_url, api_key)) from e
    except Exception as e:
        raise ProviderError(f"Request failed: {e}") from e


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
            result.append({"role": m.role, "content": str(m.content)})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


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


class OpenAIRawProvider(LLMProvider):
    """OpenAI-compatible provider using urllib."""

    name = "openai-raw"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        if not self.api_key:
            raise ProviderError(
                "OpenAI API key not found. Set OPENAI_API_KEY or configure provider.api_key."
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
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(_to_openai_messages(messages))

        loop = asyncio.get_event_loop()

        async def _do():
            data = await loop.run_in_executor(
                _EXECUTOR,
                _sync_request,
                self.api_key, self.base_url, model, api_messages, tools, max_tokens, self.timeout,
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
