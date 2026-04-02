"""OpenAI-compatible provider using the official openai SDK (optional).

Requires: pip install hushclaw[openai]

Covers OpenAI, OpenRouter, Together, Groq, MiniMax, and any other
OpenAI-compatible endpoint.  The SDK handles Authorization headers, retries,
and streaming natively — no manual urllib plumbing.

provider_label lets callers (e.g. the registry) inject the friendly name used
in log messages and error text so that MiniMax errors say "minimax" rather
than "openai-sdk".
"""
from __future__ import annotations

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall
from hushclaw.util.logging import get_logger

log = get_logger("providers.openai_sdk")


def _to_sdk_messages(messages: list[Message]) -> list[dict]:
    """Convert HushClaw messages to OpenAI SDK chat format."""
    import json
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content if isinstance(m.content, str) else json.dumps(m.content),
            })
        elif isinstance(m.content, list):
            # Anthropic-style content blocks → OpenAI format
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            msg: dict = {"role": m.role, "content": "\n".join(text_parts) or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            result.append(msg)
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def _to_sdk_tools(tools: list[dict]) -> list[dict]:
    """Convert HushClaw tool schemas (Anthropic format) to OpenAI function format."""
    result = []
    for t in tools:
        # HushClaw uses "input_schema"; OpenAI expects "parameters"
        params = t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
        result.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": params,
            },
        })
    return result


class OpenAISDKProvider(LLMProvider):
    """OpenAI-compatible provider using the official openai SDK.

    Works with OpenAI, OpenRouter, Together, Groq, MiniMax, and any other
    OpenAI-compatible API endpoint.

    provider_label: friendly name used in logs/errors (default "openai-sdk").
    Set to "minimax", "groq", etc. when instantiated via the registry for
    non-OpenAI compatible services so logs show the correct provider name.

    Requires: pip install hushclaw[openai]
    """

    name = "openai-sdk"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        provider_label: str = "openai-sdk",
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ProviderError(
                "openai SDK not installed. Run: pip install 'hushclaw[openai]'"
            ) from e

        self._label = provider_label
        # Do NOT fall back to OPENAI_API_KEY env var here — the config loader
        # already maps provider-specific env vars (MINIMAX_API_KEY, GEMINI_API_KEY,
        # OPENAI_API_KEY …) to provider.api_key before instantiation.  Reading
        # OPENAI_API_KEY here would silently bypass that logic when switching
        # providers (e.g. old key left in env → wrong key used for MiniMax).
        resolved_key = api_key.strip()
        if not resolved_key:
            raise ProviderError(
                f"{self._label} API key not found. Configure provider.api_key in "
                "hushclaw.toml or run `hushclaw serve` and use the Settings wizard."
            )

        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or "https://api.openai.com/v1",
            timeout=timeout,
            max_retries=max_retries,
        )
        self.base_url = base_url or "https://api.openai.com/v1"
        self.timeout = timeout

        log.info(
            "[%s] provider init: base_url=%s  key=%s…%s",
            self._label, self.base_url,
            resolved_key[:4],
            resolved_key[-4:],
        )

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        from openai import APIStatusError

        model = model or "gpt-4o-mini"

        api_messages: list[dict] = []
        if system:
            system_str = (
                "\n\n".join(str(s) for s in system if s)
                if isinstance(system, (list, tuple))
                else str(system)
            )
            api_messages.append({"role": "system", "content": system_str})
        api_messages.extend(_to_sdk_messages(messages))

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = _to_sdk_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except APIStatusError as e:
            log.error(
                "[%s] HTTP %d from %s: %s",
                self._label, e.status_code, self.base_url, e.message,
            )
            raise ProviderError(f"{self._label} API error {e.status_code}: {e.message}") from e
        except Exception as e:
            raise ProviderError(f"{self._label} error: {e}") from e

        choice = resp.choices[0]
        msg = choice.message
        content_text = msg.content or ""

        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            import json
            try:
                inp = json.loads(tc.function.arguments)
            except Exception:
                inp = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        usage = resp.usage
        return LLMResponse(
            content=content_text,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def list_models(self) -> list[str]:
        try:
            models = await self._client.models.list()
            return sorted(m.id for m in models.data)
        except Exception:
            return []
