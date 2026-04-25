"""Message-format transformation helpers for OpenAI-compatible providers.

Pure data functions: no I/O, no network calls.  Imported by openai_raw.py.
"""
from __future__ import annotations

import json

from hushclaw.providers.base import Message, ToolCall


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                parts.append(str(block.get("text", "")))
        if parts:
            return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _image_to_openai_block(data_uri_or_url: str) -> dict:
    """Convert a data URI or HTTPS URL to an OpenAI image_url content block."""
    return {
        "type": "image_url",
        "image_url": {"url": data_uri_or_url, "detail": "auto"},
    }


def _tool_to_responses_schema(tool: dict) -> dict:
    name = str(tool.get("name", "")).strip()
    if not name:
        return {}
    params = tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    params = dict(params)
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return {
        "type": "function",
        "name": name,
        "description": tool.get("description", ""),
        "parameters": params,
    }


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

def _content_to_responses(content) -> str | list[dict]:
    """Map OpenAI chat-completions content to Responses API content.

    Plain strings pass through unchanged.  Lists are mapped block-by-block:
    ``text`` blocks become ``input_text``, ``image_url`` blocks become
    ``input_image``.  Other block types are dropped.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)

    parts: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text") or ""
            if text:
                parts.append({"type": "input_text", "text": text})
        elif btype == "image_url":
            url = (block.get("image_url") or {}).get("url") or ""
            if url:
                parts.append({"type": "input_image", "image_url": url})
    if not parts:
        return ""
    # Single text-only block → return as plain string for cleaner payloads
    if len(parts) == 1 and parts[0]["type"] == "input_text":
        return parts[0]["text"]
    return parts


def to_responses_input(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-style messages to Responses API input items."""
    items: list[dict] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": _message_text(msg.get("content", "")),
            })
            continue

        if role not in ("user", "assistant", "system"):
            role = "user"

        items.append({
            "role": role,
            "content": _content_to_responses(msg.get("content", "")),
        })
    return items


def to_openai_messages(messages: list[Message]) -> list[dict]:
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content if isinstance(m.content, str) else json.dumps(m.content),
            })
        elif isinstance(m.content, list):
            # Anthropic-style content blocks → convert to OpenAI chat-completions format:
            # extract text into content, tool_use blocks into tool_calls array.
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    if block.get("text"):
                        text_parts.append(block["text"])
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            # Never use content=null — Gemini/Vertex OpenAI proxies map that to zero
            # ``parts`` and return 400 ("must include at least one parts field").
            text_flat = "\n".join(text_parts)
            msg_out = {"role": m.role, "content": text_flat if text_flat else ""}
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            result.append(msg_out)
        else:
            # Plain text message — inject image blocks if present
            if m.images:
                content_parts: list[dict] = [_image_to_openai_block(img) for img in m.images]
                if m.content:
                    content_parts.append({"type": "text", "text": m.content})
                result.append({"role": m.role, "content": content_parts})
            else:
                result.append({"role": m.role, "content": m.content})
    return result


def normalize_messages_for_gemini_openai_proxy(
    messages: list[dict],
    *,
    model: str = "",
    label: str = "",
) -> None:
    """In-place fix for gateways (e.g. TEX → Vertex) that translate Chat Completions
    to Gemini ``Content.parts``.  Null or empty user text becomes zero parts → 400.
    """
    for msg in messages:
        role = msg.get("role") or ""
        c = msg.get("content")
        if c is None:
            msg["content"] = ""
            c = ""
        if isinstance(c, str) and c == "" and role == "user":
            msg["content"] = " "
        elif isinstance(c, list):
            if len(c) == 0 and role == "user":
                msg["content"] = " "

    # TEX routes google/gemini-* to a backend that accepts Gemini roles only:
    # role must be `user` | `model` (not system/assistant/tool).
    model_l = (model or "").lower()
    if label == "transsion" and ("gemini" in model_l or model_l.startswith("google/")):
        sys_chunks: list[str] = []
        normalized: list[dict] = []
        for msg in messages:
            role = str(msg.get("role") or "")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    sys_chunks.append(content.strip())
                continue

            # Normalize role set to Gemini-compatible values.
            if role == "assistant":
                msg["role"] = "model"
            elif role == "tool":
                txt = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                msg = {"role": "user", "content": f"[tool_result]\n{txt}" if txt else "[tool_result]"}
            elif role not in ("user", "model"):
                msg["role"] = "user"

            c2 = msg.get("content")
            if c2 is None:
                c2 = ""
            if isinstance(c2, str):
                msg["content"] = c2 if c2 else " "
            elif isinstance(c2, list) and len(c2) == 0:
                msg["content"] = " "
            normalized.append(msg)

        if sys_chunks:
            sys_text = "[system]\n" + "\n\n".join(sys_chunks)
            if normalized and normalized[0].get("role") == "user" and isinstance(normalized[0].get("content"), str):
                u = normalized[0]["content"] or " "
                normalized[0]["content"] = f"{sys_text}\n\n{u}"
            else:
                normalized.insert(0, {"role": "user", "content": sys_text})
        messages[:] = normalized


def sanitize_openai_messages_for_chat(messages: list[dict]) -> None:
    """Sanitize assistant tool_calls for strict OpenAI-compatible gateways."""
    for msg in messages:
        if str(msg.get("role") or "") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        sanitized_calls: list[dict] = []
        for i, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name", "")).strip()
            if not name:
                continue
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, str):
                args = raw_args.strip() or "{}"
            else:
                try:
                    args = json.dumps(raw_args if raw_args is not None else {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
            try:
                json.loads(args)
            except Exception:
                args = "{}"
            sanitized_calls.append({
                "id": str(tc.get("id", "") or f"call_{i}"),
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
        if sanitized_calls:
            msg["tool_calls"] = sanitized_calls
        else:
            msg.pop("tool_calls", None)


def parse_response_payload(data: dict) -> tuple[str, list[ToolCall], int, int]:
    """Parse both chat-completions and responses payloads."""
    if "choices" in data:
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content_text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                inp = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), input=inp))
        usage = data.get("usage", {})
        return (
            content_text,
            tool_calls,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    content_text = data.get("output_text") or ""
    tool_calls = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            try:
                inp = json.loads(item.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {}
            call_id = item.get("call_id") or item.get("id", "")
            tool_calls.append(ToolCall(id=call_id, name=item.get("name", ""), input=inp))
        elif not content_text and item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text") and block.get("text"):
                    content_text += str(block.get("text"))

    usage = data.get("usage", {})
    in_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
    return content_text, tool_calls, in_tok, out_tok
