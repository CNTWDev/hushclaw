"""Async tool executor with timeout and error isolation."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any

from hushclaw.tools.base import ToolDefinition, ToolResult
from hushclaw.tools.builtins.file_tools import register_download_path
from hushclaw.util.logging import get_logger

log = get_logger("tools.executor")

_ARTIFACT_JSON_KEYS = ("path", "output_path", "file_path", "screenshot_path", "artifact_path", "file")
_ARTIFACT_PATH_RE = re.compile(
    r"(?:(?:~|/|\./|\.\./)[^\s\n\"'<>]+\.[A-Za-z0-9]{1,10})"
)
_ARTIFACT_TOOLS = {"write_file", "browser_screenshot", "run_shell"}


class ToolExecutor:
    def __init__(self, registry, timeout: int = 30) -> None:
        self.registry = registry
        self.timeout = timeout
        self._context: dict[str, Any] = {}

    def set_context(self, **kwargs: Any) -> None:
        """Inject context objects (e.g. memory_store, config) for tools."""
        self._context.update(kwargs)

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Return a context value by key (public accessor, avoids private _context access)."""
        return self._context.get(key, default)

    async def execute_single(self, name: str, arguments: dict) -> ToolResult:
        """Execute a single tool call, identical to :meth:`execute` but intended
        for direct (non-LLM-driven) invocations such as REPL ``direct_tool`` dispatch."""
        return await self.execute(name, arguments)

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        td: ToolDefinition | None = self.registry.get(name)
        if td is None:
            return ToolResult.error(f"Unknown tool: {name!r}")

        # Inject context variables that the function accepts
        sig = inspect.signature(td.fn)
        kwargs = dict(arguments or {})
        for ctx_key, ctx_val in self._context.items():
            if ctx_key in sig.parameters:
                kwargs[ctx_key] = ctx_val
        kwargs = self._normalize_kwargs(sig, kwargs, name)

        # Per-tool timeout overrides the global executor timeout.
        # timeout=0 means no timeout (used for tools that await sub-agent LLM calls).
        effective_timeout = td.timeout if td.timeout is not None else self.timeout
        use_timeout = effective_timeout > 0

        try:
            if td.is_async:
                coro = td.fn(**kwargs)
                result = await (asyncio.wait_for(coro, timeout=effective_timeout) if use_timeout else coro)
            else:
                loop = asyncio.get_event_loop()
                fut = loop.run_in_executor(None, lambda: td.fn(**kwargs))
                result = await (asyncio.wait_for(fut, timeout=effective_timeout) if use_timeout else fut)
        except asyncio.TimeoutError:
            log.warning("Tool %s timed out after %ss", name, effective_timeout)
            return ToolResult.error(f"Tool {name!r} timed out after {effective_timeout}s")
        except asyncio.CancelledError:
            raise  # must re-raise so asyncio task management works correctly
        except Exception as e:
            log.error("Tool %s raised %s: %s", name, type(e).__name__, e, exc_info=True)
            return ToolResult.error(f"Tool {name!r} raised {type(e).__name__}: {e}")

        if isinstance(result, ToolResult):
            return self._maybe_attach_download(name, kwargs, result)
        return self._maybe_attach_download(name, kwargs, ToolResult.ok(result))

    def _maybe_attach_download(self, tool_name: str, arguments: dict, result: ToolResult) -> ToolResult:
        """Auto-register generated files so the UI can always offer a download."""
        if result.is_error or tool_name not in _ARTIFACT_TOOLS:
            return result
        cfg = self._context.get("_config")
        if cfg is None or getattr(getattr(cfg, "server", None), "upload_dir", None) is None:
            return result
        if self._content_has_download(result.content):
            return result

        candidates = self._candidate_artifact_paths(tool_name, arguments, result.content)
        metas: list[dict] = []
        for raw_path in candidates:
            resolved = self._resolve_existing_file(raw_path)
            if resolved is None:
                continue
            try:
                meta = register_download_path(resolved, _config=cfg, display_name=resolved.name)
            except Exception as exc:
                log.debug("artifact auto-register skipped for %s (%s): %s", tool_name, resolved, exc)
                continue
            if meta["url"] not in {m.get("url") for m in metas}:
                metas.append(meta)
        if metas:
            return ToolResult(
                content=self._merge_download_payload(result.content, metas),
                is_error=False,
            )
        return result

    @staticmethod
    def _content_has_download(content: str) -> bool:
        try:
            parsed = json.loads((content or "").strip())
        except Exception:
            return "/files/" in (content or "")
        if isinstance(parsed, dict):
            if isinstance(parsed.get("download"), dict):
                meta = parsed["download"]
                return isinstance(meta.get("url"), str) and meta["url"].startswith("/files/")
            if isinstance(parsed.get("downloads"), list):
                return any(
                    isinstance(item, dict)
                    and isinstance(item.get("url"), str)
                    and item["url"].startswith("/files/")
                    for item in parsed["downloads"]
                )
            return isinstance(parsed.get("url"), str) and parsed["url"].startswith("/files/")
        return False

    @classmethod
    def _candidate_artifact_paths(cls, tool_name: str, arguments: dict, content: str) -> list[str]:
        paths: list[str] = []
        if tool_name == "write_file":
            path_arg = str(arguments.get("path") or "").strip()
            if path_arg and not path_arg.startswith("/files/"):
                paths.append(path_arg)

        try:
            parsed = json.loads((content or "").strip())
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for key in _ARTIFACT_JSON_KEYS:
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    paths.append(value.strip())

        for match in _ARTIFACT_PATH_RE.findall(content or ""):
            if match not in paths:
                paths.append(match)
        return paths

    @staticmethod
    def _resolve_existing_file(raw_path: str) -> Path | None:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            resolved = p.resolve()
        except Exception:
            resolved = p
        if resolved.exists() and resolved.is_file():
            return resolved
        return None

    @staticmethod
    def _merge_download_payload(content: str, metas: list[dict]) -> str:
        text = str(content or "").strip()
        try:
            parsed = json.loads(text)
        except Exception:
            payload: dict[str, Any] = {"message": text} if text else {}
            if len(metas) == 1:
                payload["download"] = metas[0]
            else:
                payload["downloads"] = metas
            return json.dumps(payload, ensure_ascii=False)

        if isinstance(parsed, dict):
            if len(metas) == 1:
                if "download" not in parsed and "downloads" not in parsed:
                    parsed["download"] = metas[0]
            elif "downloads" not in parsed and "download" not in parsed:
                parsed["downloads"] = metas
            return json.dumps(parsed, ensure_ascii=False)
        payload: dict[str, Any] = {"message": text}
        if len(metas) == 1:
            payload["download"] = metas[0]
        else:
            payload["downloads"] = metas
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _normalize_kwargs(sig: inspect.Signature, kwargs: dict, tool_name: str) -> dict:
        """Defensive normalization for model-generated tool arguments.

        - Accept common aliases (queries/keywords -> query, top_k/k -> limit)
        - Drop unknown keys for tools that do not accept **kwargs
        """
        out = dict(kwargs)
        params = sig.parameters

        # query aliases
        if "query" in params and "query" not in out:
            for alias in ("queries", "keywords", "keyword", "search_query", "question", "text", "skill_name"):
                if alias not in out:
                    continue
                v = out.get(alias)
                if isinstance(v, list):
                    q = " ".join(str(x).strip() for x in v if str(x).strip())
                else:
                    q = str(v).strip() if v is not None else ""
                if q:
                    out["query"] = q
                    break

        # title aliases (LLMs sometimes use "name", "task", "item", "text")
        # NOTE: "content" is intentionally excluded here — it is a distinct required
        # parameter for tools like remember/write_file and has its own alias block below.
        if "title" in params and "title" not in out:
            for alias in ("name", "task", "item", "text", "todo", "description"):
                # Don't steal an alias that is also a valid param on this tool
                if alias in out and alias not in params:
                    out["title"] = str(out[alias]).strip()
                    break
            # Use "content" as title alias ONLY when this tool has no content param
            if "title" not in out and "content" not in params and "content" in out:
                out["title"] = str(out["content"]).strip()

        # content aliases (remember, write_file, remember_skill, etc.)
        # Fired only when the tool actually declares a `content` parameter.
        if "content" in params and "content" not in out:
            for alias in ("text", "note", "body", "message", "information", "fact", "data"):
                if alias in out and alias not in params:
                    out["content"] = str(out[alias]).strip()
                    break

        # task aliases (LLMs often use message/prompt/instruction/input/text for task delegation)
        if "task" in params and "task" not in out:
            for alias in ("message", "prompt", "instruction", "input", "text", "content", "query", "request"):
                if alias in out and alias not in params:
                    out["task"] = str(out[alias]).strip()
                    break

        # limit aliases
        if "limit" in params and "limit" not in out:
            for alias in ("top_k", "topk", "k", "max_results", "n"):
                if alias in out:
                    try:
                        out["limit"] = int(out[alias])
                    except Exception:
                        pass
                    break

        accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if accepts_varkw:
            return out

        allowed = set(params.keys())
        dropped = [k for k in out.keys() if k not in allowed]
        if dropped:
            log.warning("Tool %s dropping unexpected kwargs: %s", tool_name, ", ".join(dropped))
            out = {k: v for k, v in out.items() if k in allowed}
        return out
