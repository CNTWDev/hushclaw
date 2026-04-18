"""Abstract base classes for LLM providers."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, TypeVar

_log = logging.getLogger("hushclaw.providers")
_T = TypeVar("_T")


async def _with_retry(
    fn: Callable[[], "asyncio.coroutine[_T]"],
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_errors: tuple[type[Exception], ...] | None = None,
) -> "_T":
    """
    Retry an async callable with exponential back-off.

    Retries on ``ProviderError`` whose message contains typical transient
    indicators (timeout, rate limit, 5xx) unless *retryable_errors* is given.

    Args:
        fn: Zero-argument async callable to call.
        max_retries: Maximum number of additional attempts (0 = no retry).
        base_delay: Initial delay in seconds; doubles on each retry.
        retryable_errors: Exception types to retry on. Defaults to ProviderError
            with transient keywords.
    """
    # Import here to avoid circular import at module load time
    from hushclaw.exceptions import ProviderError

    if retryable_errors is None:
        retryable_errors = (ProviderError,)

    _TRANSIENT_KEYWORDS = (
        "timeout",
        "timed out",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "connection",
        "broken pipe",
        "unexpected eof",
        "eof occurred in violation of protocol",
        "ssl: unexpected_eof_while_reading",
    )

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except tuple(retryable_errors) as e:  # type: ignore[misc]
            msg = str(e).lower()
            is_transient = any(kw in msg for kw in _TRANSIENT_KEYWORDS)
            if attempt >= max_retries or not is_transient:
                raise
            last_exc = e
            delay = base_delay * (2 ** attempt)
            _log.warning(
                "Provider error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, max_retries + 1, delay, e,
            )
            await asyncio.sleep(delay)

    # Should not reach here
    raise last_exc  # type: ignore[misc]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    # Gemini "thinking" models embed an opaque thought_signature bytes on the
    # function_call Part.  We carry it here so _to_gemini_contents can echo it
    # back verbatim; other providers leave it as b"".
    thought_signature: bytes = field(default=b"")


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str | list  # str for text, list for mixed content blocks
    tool_call_id: str | None = None   # for tool result messages
    tool_name: str | None = None
    # Multimodal: list of data URIs ("data:image/jpeg;base64,...") or HTTPS URLs.
    # Populated only for user-role messages. Provider formatters inject image blocks.
    images: list[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    content: str
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "stop_sequence"
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StreamEvent:
    type: str   # "text" | "tool_start" | "tool_done" | "done"
    text: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_result: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        """Send messages and return the full response."""

    def rotate_credential(self, new_key: str) -> bool:
        """Swap the active API key for credential-pool rotation.

        Returns True if the provider supports runtime key rotation,
        False if the key is baked in at construction and cannot be changed.
        Providers that store ``self.api_key`` as a mutable attribute should
        override this to return True after updating the attribute.
        """
        if hasattr(self, "api_key"):
            self.api_key = new_key  # type: ignore[attr-defined]
            return True
        return False

    async def list_models(self) -> list[str]:
        """Return available model IDs. Default: empty list (no listing support)."""
        return []

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream text chunks. Default: non-streaming fallback."""
        resp = await self.complete(messages, system, tools)
        yield resp.content
