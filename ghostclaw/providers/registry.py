"""Provider registry: plugin-style registration + lazy loading."""
from __future__ import annotations

from typing import Callable, Type

from ghostclaw.config.schema import ProviderConfig
from ghostclaw.exceptions import ProviderError
from ghostclaw.providers.base import LLMProvider

# ---------------------------------------------------------------------------
# Plugin-style registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, Callable[[ProviderConfig], LLMProvider]] = {}


def register_provider(name: str) -> Callable:
    """Decorator to register a provider factory under *name*."""
    def decorator(factory: Callable[[ProviderConfig], LLMProvider]) -> Callable:
        _PROVIDERS[name] = factory
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Built-in provider factories (registered at import time)
# ---------------------------------------------------------------------------

def _anthropic_raw(config: ProviderConfig) -> LLMProvider:
    from ghostclaw.providers.anthropic_raw import AnthropicRawProvider
    return AnthropicRawProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.anthropic.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _anthropic_sdk(config: ProviderConfig) -> LLMProvider:
    from ghostclaw.providers.anthropic_sdk import AnthropicSDKProvider
    return AnthropicSDKProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
    )


def _ollama(config: ProviderConfig) -> LLMProvider:
    from ghostclaw.providers.ollama import OllamaProvider
    return OllamaProvider(
        base_url=config.base_url or "http://localhost:11434",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _openai_raw(config: ProviderConfig) -> LLMProvider:
    from ghostclaw.providers.openai_raw import OpenAIRawProvider
    return OpenAIRawProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _openai_sdk(config: ProviderConfig) -> LLMProvider:
    from ghostclaw.providers.openai_sdk import OpenAISDKProvider
    return OpenAISDKProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _aigocode_compat(config: ProviderConfig) -> LLMProvider:
    """AIGOCODE is OpenAI-compatible — route via openai-sdk with its default base URL."""
    from ghostclaw.providers.openai_sdk import OpenAISDKProvider
    return OpenAISDKProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.aigocode.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


# Register built-ins under their canonical names and aliases
_PROVIDERS.update({
    "anthropic-raw": _anthropic_raw,
    "anthropic":     _anthropic_raw,
    "anthropic-sdk": _anthropic_sdk,
    "ollama":        _ollama,
    "openai-raw":    _openai_raw,
    "openai-sdk":    _openai_sdk,
    "openai":        _openai_sdk,       # default "openai" now uses the SDK
    "aigocode-raw":  _aigocode_compat,  # legacy alias → openai-sdk with AIGOCODE base URL
    "aigocode":      _aigocode_compat,
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_provider(config: ProviderConfig) -> LLMProvider:
    """Instantiate and return the configured LLM provider."""
    factory = _PROVIDERS.get(config.name)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDERS))
        raise ProviderError(
            f"Unknown provider: {config.name!r}. Valid options: {valid}"
        )
    return factory(config)
