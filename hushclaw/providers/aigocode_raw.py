"""AIGOCODE provider — Anthropic API proxy at api.aigocode.com."""
from __future__ import annotations

import os

from hushclaw.exceptions import ProviderError
from hushclaw.providers.anthropic_raw import AnthropicRawProvider


class AIGOCODERawProvider(AnthropicRawProvider):
    """AIGOCODE provider: Anthropic-compatible proxy at api.aigocode.com."""

    name = "aigocode-raw"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.aigocode.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        resolved_key = (
            api_key
            or os.environ.get("AIGOCODE_API_KEY", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if not resolved_key:
            raise ProviderError(
                "AIGOCODE API key not found. Set AIGOCODE_API_KEY or configure provider.api_key."
            )
        super().__init__(
            api_key=resolved_key,
            base_url=base_url or "https://api.aigocode.com/v1",
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
