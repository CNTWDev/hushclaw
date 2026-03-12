"""Ollama local provider — urllib, no SDK."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import asyncio
from concurrent.futures import ThreadPoolExecutor

from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall, _with_retry


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ghostclaw-ollama")


def _sync_request(
    base_url: str,
    model: str,
    messages: list[dict],
    system: str,
    tools: list[dict] | None,
    max_tokens: int,
    timeout: int,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        raise ProviderError(f"Cannot connect to Ollama at {base_url}: {e}") from e
    except Exception as e:
        raise ProviderError(f"Ollama request failed: {e}") from e


def _sync_list_models(base_url: str, timeout: int) -> list[str]:
    req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


class OllamaProvider(LLMProvider):
    """Ollama local inference provider."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        **_,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        model = model or "llama3.2"
        api_messages = []
        for m in messages:
            role = "user" if m.role == "tool" else m.role
            content = m.content if isinstance(m.content, str) else json.dumps(m.content)
            api_messages.append({"role": role, "content": content})

        loop = asyncio.get_event_loop()

        async def _do():
            data = await loop.run_in_executor(
                _EXECUTOR,
                _sync_request,
                self.base_url, model, api_messages, system, tools, max_tokens, self.timeout,
            )
            msg = data.get("message", {})
            content_text = msg.get("content", "")
            tool_calls: list[ToolCall] = []
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    input=fn.get("arguments", {}),
                ))
            stop_reason = "end_turn" if data.get("done") else "max_tokens"
            return LLMResponse(
                content=content_text,
                stop_reason=stop_reason,
                tool_calls=tool_calls,
            )

        return await _with_retry(_do, self.max_retries, self.retry_base_delay)

    async def list_models(self) -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _EXECUTOR, _sync_list_models, self.base_url, self.timeout
        )
