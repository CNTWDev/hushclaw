"""Session-stable model tool surface.

The registry may contain many tools, but sending every schema on every provider
request is expensive and makes prompt caching fragile. A ToolSurfaceSnapshot is
frozen for a session and can be restored from its persisted schema JSON. Common
tools stay directly visible; long-tail tools are reached through the stable
``tool_search`` / ``tool_call`` bridge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from hushclaw.providers.base import ToolCall as ProviderToolCall
from hushclaw.util.logging import get_logger

log = get_logger("runtime.tool_surface")

BRIDGE_CALL_NAME = "tool_call"
BRIDGE_SEARCH_NAME = "tool_search"

DEFAULT_EAGER_TOOLS = (
    BRIDGE_SEARCH_NAME,
    BRIDGE_CALL_NAME,
    "remember",
    "recall",
    "search_notes",
    "session_search",
    "get_time",
    "search_files",
    "read_file",
    "write_file",
    "edit_document",
    "list_dir",
    "run_shell",
    "web_search",
    "fetch_url",
    "jina_read",
    "research_web",
    "search_batch",
    "read_batch",
    "search_skills",
    "use_skill",
    "skill_view",
)

_BRIDGE_HINT = """\
## On-demand tools
The runtime keeps common tools directly available and hides long-tail tools to
reduce latency and preserve prompt caching. Use `tool_search` to find a hidden
tool and inspect its input schema. Invoke a hidden tool with `tool_call`, passing
the exact tool name and an `arguments` object. Never guess argument names when a
schema is available. Runtime policy and approvals apply to the underlying tool.
"""


@dataclass(frozen=True, slots=True)
class ToolSurfaceStats:
    mode: str
    registry_tools: int
    visible_tools: int
    full_schema_tokens: int
    visible_schema_tokens: int
    fingerprint: str

    def to_perf(self) -> dict[str, int | str]:
        return {
            "tool_surface_mode": self.mode,
            "tool_registry_count": self.registry_tools,
            "tool_visible_count": self.visible_tools,
            "tool_schema_tokens": self.visible_schema_tokens,
            "tool_full_schema_tokens": self.full_schema_tokens,
            "tool_surface_fingerprint": self.fingerprint,
        }


def _schema_tokens(schemas: list[dict]) -> int:
    if not schemas:
        return 0
    encoded = json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return max(1, (len(encoded) + 3) // 4)


def _fingerprint(schemas: list[dict]) -> str:
    import hashlib

    encoded = json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class ToolSurfaceSnapshot:
    """Immutable provider-facing tool surface for one conversation session."""

    def __init__(
        self,
        registry,
        *,
        mode: str = "auto",
        schema_budget_tokens: int = 6_000,
        eager_tools: list[str] | tuple[str, ...] | None = None,
        frozen_schemas: list[dict] | None = None,
        frozen_mode: str = "",
    ) -> None:
        full = list(registry.to_api_schemas()) if registry is not None else []
        full_tokens = _schema_tokens(full)
        if frozen_schemas is not None:
            # A resumed conversation reuses the exact provider-facing JSON it
            # started with, even if plugins or skill tools changed meanwhile.
            visible = [dict(schema) for schema in frozen_schemas if isinstance(schema, dict)]
            resolved_mode = str(frozen_mode or "all")
        else:
            requested_mode = str(mode or "auto").strip().lower()
            if requested_mode not in {"auto", "all", "bridge"}:
                requested_mode = "auto"
            resolved_mode = requested_mode
            if requested_mode == "auto":
                resolved_mode = "bridge" if full_tokens > max(256, int(schema_budget_tokens)) else "all"

            if resolved_mode == "bridge":
                eager = set(eager_tools or DEFAULT_EAGER_TOOLS)
                visible = [schema for schema in full if str(schema.get("name") or "") in eager]
                # A broken custom enabled-list must not leave the model with a bridge
                # hint but no bridge.  If either schema is unavailable, preserve the
                # complete surface for correctness.
                visible_names = {str(schema.get("name") or "") for schema in visible}
                if not {BRIDGE_SEARCH_NAME, BRIDGE_CALL_NAME}.issubset(visible_names):
                    resolved_mode = "all"
                    visible = full
            else:
                visible = full

        self._schemas = tuple(visible)
        self._provider_names_by_call_id: dict[str, str] = {}
        self.stats = ToolSurfaceStats(
            mode=resolved_mode,
            registry_tools=len(full),
            visible_tools=len(visible),
            full_schema_tokens=full_tokens,
            visible_schema_tokens=_schema_tokens(visible),
            fingerprint=_fingerprint(visible),
        )
        log.info(
            "tool surface: mode=%s visible=%d/%d schema=%d/%d tokens fingerprint=%s",
            self.stats.mode,
            self.stats.visible_tools,
            self.stats.registry_tools,
            self.stats.visible_schema_tokens,
            self.stats.full_schema_tokens,
            self.stats.fingerprint,
        )

    @property
    def prompt_hint(self) -> str:
        return _BRIDGE_HINT if self.stats.mode == "bridge" else ""

    def to_record(self) -> dict:
        """Return the exact JSON-safe surface persisted for session resumes."""
        return {
            "mode": self.stats.mode,
            "schemas": [dict(schema) for schema in self._schemas],
            "fingerprint": self.stats.fingerprint,
        }

    def schemas(self, allowed_tools: frozenset[str] | None = None) -> list[dict] | None:
        schemas = list(self._schemas)
        if allowed_tools is not None:
            schemas = [schema for schema in schemas if schema.get("name") in allowed_tools]
        return schemas or None

    def resolve_execution_calls(self, calls: list[ProviderToolCall]) -> list[ProviderToolCall]:
        """Resolve bridge calls while retaining their provider-visible identity."""
        resolved: list[ProviderToolCall] = []
        for call in calls or []:
            if call.name != BRIDGE_CALL_NAME:
                resolved.append(call)
                continue
            target = str((call.input or {}).get("name") or "").strip()
            arguments = (call.input or {}).get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}
            if not target or target == BRIDGE_CALL_NAME or not isinstance(arguments, dict):
                # Let the registered bridge stub return a useful structured error.
                resolved.append(call)
                continue
            self._provider_names_by_call_id[call.id] = BRIDGE_CALL_NAME
            resolved.append(ProviderToolCall(
                id=call.id,
                name=target,
                input=dict(arguments),
                thought_signature=call.thought_signature,
            ))
        return resolved

    def provider_tool_name(self, call_id: str, execution_name: str) -> str:
        # One provider result consumes one call identity. Popping avoids stale
        # mappings if an SDK reuses deterministic call IDs on a later round.
        return self._provider_names_by_call_id.pop(str(call_id or ""), execution_name)
