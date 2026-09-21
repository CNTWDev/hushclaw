"""OpenAI-compatible VoxNexus provider using the shared VoxAuth session."""
from __future__ import annotations

import asyncio
import json

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMProvider, LLMResponse
from hushclaw.providers.openai_transforms import to_openai_messages, parse_response_payload
from hushclaw.providers.voxnexus_auth import get_session


class VoxNexusProvider(LLMProvider):
    name = "voxnexus"

    def __init__(self, config):
        # Defer session/keychain access so a logged-out app can still start Settings.
        self.config = config

    @property
    def session(self):
        return get_session(self.config)

    def _payload(self, messages, system, tools, max_tokens, model):
        if not model:
            raise ProviderError("请在 Settings → VoxNexus 选择模型。")
        items = to_openai_messages(messages)
        if system:
            text = "\n\n".join(str(s) for s in system if s) if isinstance(system, (tuple, list)) else str(system)
            items.insert(0, {"role": "system", "content": text})
        payload = {"model": model, "messages": items, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
            }} for t in tools]
        return payload

    @staticmethod
    def _response(data):
        text, calls, inp, out, stop = parse_response_payload(data)
        return LLMResponse(text, "tool_use" if calls else stop, calls, inp, out)

    async def complete(self, messages, system="", tools=None, max_tokens=4096, model=None):
        response = await self.session.open("/v1/chat/completions",
            self._payload(messages, system, tools, max_tokens, model), self.config.timeout)
        def read():
            with response:
                return json.load(response)
        return self._response(await asyncio.to_thread(read))

    async def list_models(self):
        result = await self.session.request("/v1/models")
        return [m["id"] for m in result.get("data", []) if isinstance(m, dict) and m.get("id")]

    async def stream_complete(self, messages, system="", tools=None, max_tokens=4096, model=None):
        payload = self._payload(messages, system, tools, max_tokens, model)
        payload["stream"] = True
        response = await self.session.open("/v1/chat/completions", payload, self.config.timeout)
        text, calls, usage, finish = [], {}, {}, None
        def read_line():
            return response.readline()
        try:
            while True:
                line = await asyncio.to_thread(read_line)
                if not line:
                    break
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise ProviderError("VoxNexus 流式响应中断，请重试。")
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    if delta.get("content"):
                        text.append(delta["content"])
                        yield delta["content"]
                    for part in delta.get("tool_calls") or []:
                        entry = calls.setdefault(part["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if part.get("id"):
                            entry["id"] = part["id"]
                        for key in ("name", "arguments"):
                            entry["function"][key] += (part.get("function") or {}).get(key) or ""
            if finish is None:
                raise ProviderError("VoxNexus 流式响应未完成，请重试。")
            yield self._response({"choices": [{"finish_reason": finish, "message": {
                "content": "".join(text), "tool_calls": [calls[i] for i in sorted(calls)]}}], "usage": usage})
        finally:
            response.close()
