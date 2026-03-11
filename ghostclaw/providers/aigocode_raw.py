"""AIGOCODE provider — OpenAI-compatible auth, Responses API protocol."""
from __future__ import annotations

import asyncio
import os

from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMResponse, Message, _with_retry
from ghostclaw.providers.openai_raw import (
    OpenAIRawProvider,
    _EXECUTOR,
    _normalize_base_url,
    _parse_response_payload,
    _sync_request_responses,
    _to_openai_messages,
)


class AIGOCODERawProvider(OpenAIRawProvider):
    """AIGOCODE provider that always uses /v1/responses."""

    name = "aigocode-raw"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.aigocode.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("AIGOCODE_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        if not self.api_key:
            raise ProviderError(
                "AIGOCODE API key not found. Set AIGOCODE_API_KEY or configure provider.api_key."
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
                _sync_request_responses,
                self.api_key,
                self.base_url,
                model,
                api_messages,
                tools,
                max_tokens,
                self.timeout,
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
