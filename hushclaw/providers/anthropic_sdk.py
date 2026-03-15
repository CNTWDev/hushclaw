"""Anthropic provider using the official anthropic SDK (optional)."""
from __future__ import annotations

import os

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall


class AnthropicSDKProvider(LLMProvider):
    """Requires: pip install anthropic"""

    name = "anthropic-sdk"

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        timeout: int = 120,
    ) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise ProviderError(
                "anthropic SDK not installed. Run: pip install hushclaw[anthropic]"
            ) from e

        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=base_url,
            timeout=timeout,
        )

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        import anthropic

        model = model or "claude-sonnet-4-6"
        api_messages = []
        for m in messages:
            if m.role == "tool":
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": m.content if isinstance(m.content, str) else str(m.content),
                        }
                    ],
                })
            else:
                api_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        resp = await self._client.messages.create(**kwargs)

        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if hasattr(block, "text"):
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMResponse(
            content=content_text,
            stop_reason=resp.stop_reason or "end_turn",
            tool_calls=tool_calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
