"""Anthropic Claude provider — zero dependencies, urllib only."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall, _with_retry


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ghostclaw-http")


def _messages_to_anthropic(messages: list[Message]) -> list[dict]:
    """Convert internal Message list to Anthropic API format."""
    result = []
    for m in messages:
        if m.role == "tool":
            # Tool result — Anthropic expects user-role with tool_result content
            result.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content if isinstance(m.content, str) else json.dumps(m.content),
                    }
                ],
            })
        elif isinstance(m.content, list):
            result.append({"role": m.role, "content": m.content})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def _parse_response(data: dict) -> LLMResponse:
    content_text = ""
    tool_calls: list[ToolCall] = []

    for block in data.get("content", []):
        if block.get("type") == "text":
            content_text += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input", {}),
            ))

    usage = data.get("usage", {})
    return LLMResponse(
        content=content_text,
        stop_reason=data.get("stop_reason", "end_turn"),
        tool_calls=tool_calls,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def _build_system_payload(system: "str | tuple[str, str]") -> "str | list[dict] | None":
    """
    Convert system prompt to Anthropic API format.
    If system is a (stable, dynamic) tuple, emit content blocks with cache_control
    on the stable part so Anthropic's KV cache can reuse it across turns.
    """
    if not system:
        return None
    if isinstance(system, tuple):
        stable, dynamic = system
        blocks = []
        if stable:
            blocks.append({
                "type": "text",
                "text": stable,
                "cache_control": {"type": "ephemeral"},
            })
        if dynamic:
            blocks.append({"type": "text", "text": dynamic})
        return blocks if blocks else None
    return system if system else None


def _sync_request(
    api_key: str,
    model: str,
    messages: list[dict],
    system: "str | tuple[str, str]",
    tools: list[dict] | None,
    max_tokens: int,
    base_url: str,
    timeout: int,
) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    system_value = _build_system_payload(system)
    if system_value:
        payload["system"] = system_value
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Anthropic API error {e.code}: {body}") from e
    except Exception as e:
        raise ProviderError(f"Request failed: {e}") from e


class AnthropicRawProvider(LLMProvider):
    """Anthropic provider using urllib — no SDK required."""

    name = "anthropic-raw"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    async def complete(
        self,
        messages: list[Message],
        system: "str | tuple[str, str]" = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        if not self.api_key:
            raise ProviderError(
                "Anthropic API key not configured. Use the setup wizard or set ANTHROPIC_API_KEY."
            )
        model = model or "claude-sonnet-4-6"
        api_messages = _messages_to_anthropic(messages)

        loop = asyncio.get_event_loop()

        async def _do():
            data = await loop.run_in_executor(
                _EXECUTOR,
                _sync_request,
                self.api_key, model, api_messages, system, tools, max_tokens,
                self.base_url, self.timeout,
            )
            return _parse_response(data)

        return await _with_retry(_do, self.max_retries, self.retry_base_delay)

    def _sync_sse_stream(self, payload: dict):
        """Synchronous generator: yields text chunks from Anthropic SSE stream."""
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").rstrip("\n\r")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                    elif etype == "message_stop":
                        break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ProviderError(f"Anthropic SSE error {e.code}: {body}") from e
        except Exception as e:
            raise ProviderError(f"SSE stream failed: {e}") from e

    async def stream(
        self,
        messages: list[Message],
        system: "str | tuple[str, str]" = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ):
        """Real SSE streaming via urllib + asyncio.Queue bridge."""
        if not self.api_key:
            raise ProviderError(
                "Anthropic API key not configured. Use the setup wizard or set ANTHROPIC_API_KEY."
            )
        model = model or "claude-sonnet-4-6"
        api_messages = _messages_to_anthropic(messages)

        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "stream": True,
        }
        system_value = _build_system_payload(system)
        if system_value:
            payload["system"] = system_value
        if tools:
            payload["tools"] = tools

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _sentinel = object()

        def run_sync():
            try:
                for chunk in self._sync_sse_stream(payload):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _sentinel)

        loop.run_in_executor(_EXECUTOR, run_sync)

        while True:
            item = await queue.get()
            if item is _sentinel:
                break
            if isinstance(item, Exception):
                raise ProviderError(f"SSE stream error: {item}") from item
            yield item
