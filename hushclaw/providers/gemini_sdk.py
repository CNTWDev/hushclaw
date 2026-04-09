"""Google Gemini provider using the official google-genai SDK.

Requires: pip install hushclaw[gemini]  # adds google-genai>=1.0

Supports:
  - gemini-2.5-flash-preview (fast, cheap)
  - gemini-2.5-pro (most capable)
  - gemini-2.0-flash, gemini-1.5-pro, etc.
  - Tool / function calling (full schema passthrough)
  - System instructions
"""
from __future__ import annotations

import json
import os

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider, LLMResponse, Message, ToolCall
from hushclaw.util.logging import get_logger

log = get_logger("providers.gemini_sdk")

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


def _parse_data_uri(data_uri: str) -> tuple[bytes, str]:
    """Parse a data URI into (bytes, mime_type). Returns (b'', '') on failure."""
    if not data_uri.startswith("data:"):
        return b"", ""
    header, _, b64data = data_uri.partition(",")
    mime = header.removeprefix("data:").split(";")[0] or "image/jpeg"
    import base64
    try:
        return base64.b64decode(b64data), mime
    except Exception:
        return b"", ""


def _image_to_gemini_part(data_uri_or_url: str):
    """Convert a data URI or HTTPS URL to a Gemini Part."""
    from google.genai import types

    if data_uri_or_url.startswith("data:"):
        raw_bytes, mime = _parse_data_uri(data_uri_or_url)
        if raw_bytes:
            return types.Part.from_bytes(data=raw_bytes, mime_type=mime)
        return None
    # HTTPS URL — use inline_data approach via URI
    return types.Part(
        inline_data=types.Blob(mime_type="image/jpeg", data=b""),
    ) if False else types.Part(
        file_data=types.FileData(mime_type="image/jpeg", file_uri=data_uri_or_url),
    )


def _to_gemini_contents(messages: list[Message]) -> list:
    """Convert HushClaw messages to Gemini SDK Content objects."""
    from google.genai import types

    contents = []
    for m in messages:
        if m.role == "tool":
            # Tool result — wrap as function_response part under "user" role
            raw = m.content
            if isinstance(raw, str):
                try:
                    response_data = json.loads(raw)
                except Exception:
                    response_data = {"output": raw}
            else:
                response_data = raw if isinstance(raw, dict) else {"output": str(raw)}

            part = types.Part.from_function_response(
                name=m.tool_name or "tool",
                response=response_data,
            )
            contents.append(types.Content(role="user", parts=[part]))

        elif isinstance(m.content, list):
            # Mixed content blocks (Anthropic format) — flatten to text + tool_use
            parts: list = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    parts.append(types.Part.from_text(text=block["text"]))
                elif btype == "tool_use":
                    # Echo thought_signature for Gemini thinking models if present
                    sig = block.get("_thought_sig") or b""
                    if sig:
                        parts.append(types.Part(
                            function_call=types.FunctionCall(
                                name=block.get("name", ""),
                                args=block.get("input") or {},
                            ),
                            thought_signature=sig,
                        ))
                    else:
                        parts.append(types.Part.from_function_call(
                            name=block.get("name", ""),
                            args=block.get("input") or {},
                        ))
            if parts:
                role = "model" if m.role == "assistant" else "user"
                contents.append(types.Content(role=role, parts=parts))

        else:
            role = "model" if m.role == "assistant" else "user"
            parts = []
            # Inject image parts before text for user messages
            if m.images and role == "user":
                for img in m.images:
                    p = _image_to_gemini_part(img)
                    if p is not None:
                        parts.append(p)
            parts.append(types.Part.from_text(text=m.content or ""))
            contents.append(types.Content(role=role, parts=parts))
    return contents


def _to_gemini_tools(tools: list[dict]) -> list:
    """Convert HushClaw tool schemas (Anthropic format) to Gemini FunctionDeclaration."""
    from google.genai import types

    declarations = []
    for t in tools:
        schema_raw = t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}}
        declarations.append(types.FunctionDeclaration(
            name=t.get("name", ""),
            description=t.get("description", ""),
            parameters=schema_raw,
        ))
    return [types.Tool(function_declarations=declarations)]


def _stop_reason(candidate) -> str:
    """Map Gemini finish reason to HushClaw stop reason."""
    try:
        reason = str(candidate.finish_reason).upper()
    except Exception:
        return "end_turn"
    if "MAX_TOKENS" in reason:
        return "max_tokens"
    if "STOP" in reason or "END" in reason:
        return "end_turn"
    return "end_turn"


class GeminiSDKProvider(LLMProvider):
    """Google Gemini provider using the official google-genai SDK.

    Requires: pip install 'hushclaw[gemini]'
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        try:
            from google import genai  # noqa: F401
        except ImportError as e:
            raise ProviderError(
                "google-genai SDK not installed. Run: pip install 'hushclaw[gemini]'"
            ) from e

        self._api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not self._api_key:
            raise ProviderError(
                "Gemini API key not found. Set GEMINI_API_KEY or configure provider.api_key."
            )
        self._base_url = (base_url or "").strip() or _DEFAULT_BASE_URL
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

        log.info(
            "[gemini] provider init: key=%s…%s",
            self._api_key[:4],
            self._api_key[-4:],
        )

    def _client(self):
        from google import genai
        client_kwargs: dict = {"api_key": self._api_key}
        if self._base_url and self._base_url != _DEFAULT_BASE_URL:
            client_kwargs["http_options"] = {"base_url": self._base_url}
        return genai.Client(**client_kwargs)

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        from google.genai import types
        from google.genai.errors import APIError

        model = model or "gemini-2.5-flash-preview-04-17"

        system_str = ""
        if system:
            if isinstance(system, (list, tuple)):
                system_str = "\n\n".join(str(s) for s in system if s)
            else:
                system_str = str(system)

        contents = _to_gemini_contents(messages)

        config_kwargs: dict = {"max_output_tokens": max_tokens}
        if system_str:
            config_kwargs["system_instruction"] = system_str
        if tools:
            config_kwargs["tools"] = _to_gemini_tools(tools)

        generate_config = types.GenerateContentConfig(**config_kwargs)

        client = self._client()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=generate_config,
            )
            try:
                cand_count = len(response.candidates or [])
                usage = getattr(response, "usage_metadata", None)
                part_types = []
                if (
                    cand_count > 0
                    and response.candidates[0].content
                    and response.candidates[0].content.parts
                ):
                    for p in response.candidates[0].content.parts:
                        if getattr(p, "text", None):
                            part_types.append("text")
                        elif getattr(p, "function_call", None):
                            part_types.append("function_call")
                        else:
                            part_types.append(type(p).__name__)
                log.info(
                    "[gemini] response candidates=%d part_types=%s prompt_tokens=%s "
                    "candidate_tokens=%s total_tokens=%s",
                    cand_count,
                    part_types,
                    getattr(usage, "prompt_token_count", None) if usage else None,
                    getattr(usage, "candidates_token_count", None) if usage else None,
                    getattr(usage, "total_token_count", None) if usage else None,
                )
            except Exception as _e:
                log.warning("[gemini] response summary log failed: %s", _e)
        except APIError as e:
            log.error("[gemini] API error: %s", e)
            raise ProviderError(f"Gemini API error: {e}") from e
        except Exception as e:
            raise ProviderError(f"Gemini SDK error: {e}") from e

        # Parse response
        candidate = response.candidates[0] if response.candidates else None
        content_text = ""
        tool_calls: list[ToolCall] = []

        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    content_text += part.text
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args = {}
                    if fc.args:
                        try:
                            # fc.args is a MapComposite (dict-like)
                            args = dict(fc.args)
                        except Exception:
                            args = {}
                    # Preserve thought_signature for thinking models (lives on Part)
                    sig = getattr(part, "thought_signature", None) or b""
                    if isinstance(sig, str):
                        sig = sig.encode() if sig else b""
                    tool_calls.append(ToolCall(
                        id=f"fc_{fc.name}_{len(tool_calls)}",
                        name=fc.name,
                        input=args,
                        thought_signature=sig,
                    ))

        stop_reason = "tool_use" if tool_calls else (
            _stop_reason(candidate) if candidate else "end_turn"
        )

        usage = response.usage_metadata
        return LLMResponse(
            content=content_text,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        )

    async def list_models(self) -> list[str]:
        try:
            client = self._client()
            models = await client.aio.models.list()
            return sorted(
                m.name.removeprefix("models/")
                for m in models
                if "generateContent" in (m.supported_actions or [])
            )
        except Exception:
            return [
                "gemini-2.5-flash-preview-04-17",
                "gemini-2.5-pro-preview-05-06",
                "gemini-2.0-flash",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ]
