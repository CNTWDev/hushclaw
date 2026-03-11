"""OpenAI-compatible provider — urllib, no SDK."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall, _with_retry
from ghostclaw.util.ssl_context import make_ssl_context


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ghostclaw-openai")


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
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"OpenAI API error {e.code}: {body}") from e
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
        self.base_url = base_url.rstrip("/")
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

            finish = choice.get("finish_reason", "stop")
            stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
            usage = data.get("usage", {})
            return LLMResponse(
                content=content_text,
                stop_reason=stop_reason,
                tool_calls=tool_calls,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )

        return await _with_retry(_do, self.max_retries, self.retry_base_delay)
