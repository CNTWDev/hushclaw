"""Provider registry: plugin-style registration + lazy loading."""
from __future__ import annotations

from typing import Callable, Type

from hushclaw.config.schema import ProviderConfig
from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider

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
    from hushclaw.providers.anthropic_raw import AnthropicRawProvider
    return AnthropicRawProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.anthropic.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _anthropic_sdk(config: ProviderConfig) -> LLMProvider:
    from hushclaw.providers.anthropic_sdk import AnthropicSDKProvider
    return AnthropicSDKProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
    )


def _ollama(config: ProviderConfig) -> LLMProvider:
    from hushclaw.providers.ollama import OllamaProvider
    return OllamaProvider(
        base_url=config.base_url or "http://localhost:11434",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _openai_raw(config: ProviderConfig) -> LLMProvider:
    from hushclaw.providers.openai_raw import OpenAIRawProvider
    return OpenAIRawProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
        provider_label=config.name or "openai-raw",
    )


def _openai_sdk(config: ProviderConfig) -> LLMProvider:
    from hushclaw.providers.openai_sdk import OpenAISDKProvider
    return OpenAISDKProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
        provider_label=config.name or "openai-sdk",
    )


def _aigocode_compat(config: ProviderConfig) -> LLMProvider:
    """AIGOCODE is Anthropic-protocol-compatible — route via AnthropicRawProvider."""
    from hushclaw.providers.anthropic_raw import AnthropicRawProvider
    return AnthropicRawProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.aigocode.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _minimax(config: ProviderConfig) -> LLMProvider:
    """MiniMax — OpenAI-compatible API.
    Global: https://api.minimax.io/v1
    China:  https://api.minimaxi.com/v1
    base_url is set by the wizard; falls back to the global endpoint.
    """
    from hushclaw.providers.openai_sdk import OpenAISDKProvider
    return OpenAISDKProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.minimax.io/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
        provider_label="minimax",
    )


def _gemini(config: ProviderConfig) -> LLMProvider:
    """Google Gemini via the official google-genai SDK.

    Requires: pip install 'hushclaw[gemini]'
    Models: gemini-2.5-flash-preview-04-17, gemini-2.5-pro-preview-05-06, etc.
    """
    from hushclaw.providers.gemini_sdk import GeminiSDKProvider
    return GeminiSDKProvider(
        api_key=config.api_key,
        base_url=config.base_url or "",
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
    )


def _transsion(config: ProviderConfig) -> LLMProvider:
    """Transsion / TEX AI Router — OpenAI-compatible multi-model gateway.

    Credentials (api_key + base_url) are obtained via the two-step email-code
    login flow in the Settings wizard and stored in hushclaw.toml.
    Models: azure/gpt-4o-mini, azure/gpt-4.1, google/gemini-2.5-flash-lite, etc.
    """
    from hushclaw.providers.transsion import TranssionProvider
    return TranssionProvider(
        api_key=config.api_key,
        base_url=config.base_url or "https://airouter.aibotplatform.com/v1",
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
    "aigocode-raw":  _aigocode_compat,  # Anthropic-compatible proxy at api.aigocode.com/v1
    "aigocode":      _aigocode_compat,
    "minimax":       _minimax,          # MiniMax M2 series — OpenAI-compatible
    "gemini":        _gemini,           # Google Gemini via google-genai SDK
    "google":        _gemini,           # alias
    "transsion":     _transsion,        # Transsion / TEX AI Router
    "tex":           _transsion,        # alias
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
