"""Structured prompt block contract for AgentOS.

AgentOS owns the renderer and ordering rules. Distros and domains may declare
additional blocks through this narrow contract, but the kernel never imports
business modules to discover them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Literal, Mapping

PromptTier = Literal["stable", "dynamic"]
PromptOwner = Literal["kernel", "distro", "domain", "user"]
PromptContent = str | Callable[["PromptRenderContext"], str]
PromptGuard = Callable[["PromptRenderContext"], bool]

_VALID_TIERS = {"stable", "dynamic"}
_VALID_OWNERS = {"kernel", "distro", "domain", "user"}


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Provider-confirmed model behavior used by prompt guards."""

    tool_calls: bool = False
    parallel_tool_calls: bool = False
    reasoning: bool = False


@dataclass(frozen=True, slots=True)
class PromptRenderContext:
    """Runtime information available to prompt block renderers.

    Keep this context generic. Domain-specific data should be supplied by a
    domain-owned block through ``extra`` or by domain context providers, not by
    adding business fields to AgentOS.
    """

    principal: Any = None
    config: Any = None
    memory: Any = None
    session_id: str = ""
    workspace_dir: Path | None = None
    platform: str = ""
    model: str = ""
    query: str = ""
    tool_names: frozenset[str] = field(default_factory=frozenset)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    model_capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_names", frozenset(self.tool_names))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))


@dataclass(frozen=True, slots=True)
class PromptBlock:
    """One renderable prompt fragment owned by kernel, distro, domain, or user."""

    id: str
    content: PromptContent
    tier: PromptTier = "stable"
    owner: PromptOwner = "kernel"
    priority: int = 100
    cacheable: bool = True
    enabled: bool = True
    title: str = ""
    guard: PromptGuard | None = None

    def __post_init__(self) -> None:
        block_id = self.id.strip()
        if not block_id:
            raise ValueError("PromptBlock.id must not be empty")
        if self.tier not in _VALID_TIERS:
            raise ValueError(f"Unsupported prompt block tier: {self.tier!r}")
        if self.owner not in _VALID_OWNERS:
            raise ValueError(f"Unsupported prompt block owner: {self.owner!r}")
        object.__setattr__(self, "id", block_id)

    def render(self, context: PromptRenderContext) -> str:
        if not self.enabled or (self.guard is not None and not self.guard(context)):
            return ""
        value = self.content(context) if callable(self.content) else self.content
        return str(value or "").strip()

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tier": self.tier,
            "owner": self.owner,
            "priority": self.priority,
            "cacheable": self.cacheable,
            "enabled": self.enabled,
            "title": self.title,
            "guarded": self.guard is not None,
        }


@dataclass(frozen=True, slots=True)
class PromptManifestItem:
    """Observable result of evaluating one prompt block."""

    id: str
    tier: PromptTier
    owner: PromptOwner
    priority: int
    cacheable: bool
    included: bool
    chars: int
    content_hash: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tier": self.tier,
            "owner": self.owner,
            "priority": self.priority,
            "cacheable": self.cacheable,
            "included": self.included,
            "chars": self.chars,
            "content_hash": self.content_hash,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PromptAssembly:
    """Provider-ready prompt segments plus a content-free debug manifest."""

    stable: str
    dynamic: str
    manifest: tuple[PromptManifestItem, ...]

    def manifest_dict(self) -> dict[str, Any]:
        stable_hash = hashlib.sha256(self.stable.encode("utf-8")).hexdigest()[:16]
        dynamic_hash = hashlib.sha256(self.dynamic.encode("utf-8")).hexdigest()[:16]
        return {
            "items": [item.to_dict() for item in self.manifest],
            "stable_chars": len(self.stable),
            "dynamic_chars": len(self.dynamic),
            "total_chars": len(self.stable) + len(self.dynamic),
            "stable_hash": stable_hash,
            "dynamic_hash": dynamic_hash,
        }


class PromptBlockRegistry:
    """Ordered collection of prompt blocks.

    Duplicate IDs are replaced intentionally. This lets a distro override a
    kernel default without mutating the kernel module that declared it.
    """

    def __init__(self, blocks: Iterable[PromptBlock] | None = None) -> None:
        self._blocks: dict[str, PromptBlock] = {}
        if blocks:
            self.extend(blocks)

    def register(self, block: PromptBlock) -> None:
        self._blocks[block.id] = block

    def extend(self, blocks: Iterable[PromptBlock]) -> None:
        for block in blocks:
            self.register(block)

    def blocks(
        self,
        *,
        tier: PromptTier | None = None,
        owner: PromptOwner | None = None,
        include_disabled: bool = False,
    ) -> list[PromptBlock]:
        items = list(self._blocks.values())
        if tier is not None:
            items = [block for block in items if block.tier == tier]
        if owner is not None:
            items = [block for block in items if block.owner == owner]
        if not include_disabled:
            items = [block for block in items if block.enabled]
        return sorted(items, key=lambda block: (block.priority, block.owner, block.id))

    def render(self, tier: PromptTier, context: PromptRenderContext) -> str:
        rendered, _manifest = PromptAssembler(self).render_tier(tier, context)
        return rendered

    def list_blocks(
        self,
        *,
        tier: PromptTier | None = None,
        owner: PromptOwner | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            block.metadata()
            for block in self.blocks(tier=tier, owner=owner, include_disabled=include_disabled)
        ]

    def copy(self) -> "PromptBlockRegistry":
        return PromptBlockRegistry(self._blocks.values())


class PromptAssembler:
    """The single pure renderer for stable and per-turn prompt blocks."""

    def __init__(self, registry: PromptBlockRegistry) -> None:
        self._registry = registry

    def render_tier(
        self,
        tier: PromptTier,
        context: PromptRenderContext,
    ) -> tuple[str, tuple[PromptManifestItem, ...]]:
        rendered: list[str] = []
        manifest: list[PromptManifestItem] = []
        for block in self._registry.blocks(tier=tier, include_disabled=True):
            if not block.enabled:
                text = ""
                reason = "disabled"
            elif block.guard is not None and not block.guard(context):
                text = ""
                reason = "guard_false"
            else:
                value = block.content(context) if callable(block.content) else block.content
                text = str(value or "").strip()
                reason = "included" if text else "empty"
            if text:
                rendered.append(text)
            manifest.append(PromptManifestItem(
                id=block.id,
                tier=block.tier,
                owner=block.owner,
                priority=block.priority,
                cacheable=block.cacheable,
                included=bool(text),
                chars=len(text),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else "",
                reason=reason,
            ))
        return "\n\n".join(rendered), tuple(manifest)

    def assemble(self, context: PromptRenderContext) -> PromptAssembly:
        stable, stable_manifest = self.render_tier("stable", context)
        dynamic, dynamic_manifest = self.render_tier("dynamic", context)
        return PromptAssembly(
            stable=stable,
            dynamic=dynamic,
            manifest=stable_manifest + dynamic_manifest,
        )


def legacy_system_prompt_block(system_prompt: str) -> PromptBlock:
    """Wrap the existing assembled system prompt as a structured kernel block."""

    stable_prompt = (system_prompt or "").replace(" Today is {date}.", "").replace("Today is {date}.", "")
    return PromptBlock(
        id="kernel.legacy_system_prompt",
        owner="kernel",
        tier="stable",
        priority=0,
        cacheable=True,
        title="Legacy System Prompt",
        content=stable_prompt,
    )


def _looks_like_default_system_prompt(system_prompt: str) -> bool:
    """Return True when a prompt should use kernel structured defaults."""

    from hushclaw.config.system_prompt import should_reset_persisted_system_prompt

    value = (system_prompt or "").strip()
    return not value or should_reset_persisted_system_prompt(value)


def _detect_default_platform(system_prompt: str) -> str:
    from hushclaw.prompts import PLATFORM_HINTS

    value = system_prompt or ""
    for platform, hint in PLATFORM_HINTS.items():
        if hint and hint in value:
            return platform
    return ""


def _platform_hint_content(default_platform: str = "") -> Callable[[PromptRenderContext], str]:
    from hushclaw.prompts import PLATFORM_HINTS

    def render(context: PromptRenderContext) -> str:
        platform = (context.platform or default_platform or "").strip().lower()
        return PLATFORM_HINTS.get(platform, "")

    return render


def prompt_capabilities_from_tools(tool_names: Iterable[str]) -> frozenset[str]:
    """Derive prompt feature gates from the provider-visible tool surface."""

    names = frozenset(str(name or "").strip() for name in tool_names if str(name or "").strip())
    capabilities: set[str] = set()
    if names:
        capabilities.add("tools")
    if names & {"remember", "recall", "search_notes", "session_search"}:
        capabilities.add("memory_tools")
    if names & {"search_skills", "use_skill", "list_skills", "skill_view", "remember_skill"}:
        capabilities.add("skill_tools")
    if names & {"research_web", "web_search", "search_batch", "read_batch", "fetch_url", "jina_read"}:
        capabilities.add("web_tools")
    if names & {"search_files", "read_file", "write_file", "edit_document", "list_dir"}:
        capabilities.add("file_tools")
    if {"tool_search", "tool_call"}.issubset(names):
        capabilities.add("tool_bridge")
    return frozenset(capabilities)


def _has_capability(name: str) -> PromptGuard:
    return lambda context: context.model_capabilities.tool_calls and name in context.capabilities


def _has_tools(*required: str) -> PromptGuard:
    required_names = frozenset(required)
    return lambda context: (
        context.model_capabilities.tool_calls
        and required_names.issubset(context.tool_names)
    )


def default_system_prompt_blocks(platform: str = "") -> list[PromptBlock]:
    """Return the built-in system prompt as overridable structured blocks."""

    from hushclaw import prompts

    return [
        PromptBlock(
            id="kernel.identity",
            owner="kernel",
            tier="stable",
            priority=0,
            cacheable=True,
            title="Agent Identity",
            content=prompts.AGENT_IDENTITY,
        ),
        PromptBlock(
            id="kernel.response_policy",
            owner="kernel",
            tier="stable",
            priority=5,
            cacheable=True,
            title="Response Policy",
            content=prompts.RESPONSE_POLICY,
        ),
        PromptBlock(
            id="kernel.language_policy",
            owner="kernel",
            tier="stable",
            priority=10,
            cacheable=True,
            title="Language Policy",
            content=prompts.LANGUAGE_POLICY,
        ),
        PromptBlock(
            id="kernel.memory",
            owner="kernel",
            tier="stable",
            priority=20,
            cacheable=True,
            title="Memory",
            content=prompts.MEMORY_GUIDANCE,
            guard=_has_tools("remember", "recall", "session_search"),
        ),
        PromptBlock(
            id="kernel.context_use",
            owner="kernel",
            tier="stable",
            priority=30,
            cacheable=True,
            title="Context Use",
            content=prompts.CONTEXT_USE_GUIDANCE,
        ),
        PromptBlock(
            id="kernel.tool_use",
            owner="kernel",
            tier="stable",
            priority=40,
            cacheable=True,
            title="Tool Use",
            content=prompts.TOOL_USE_GUIDANCE,
            guard=_has_capability("tools"),
        ),
        PromptBlock(
            id="kernel.web_research",
            owner="kernel",
            tier="stable",
            priority=42,
            cacheable=True,
            title="Current-information Research",
            content=prompts.WEB_RESEARCH_GUIDANCE,
            guard=_has_capability("web_tools"),
        ),
        PromptBlock(
            id="kernel.file_tools",
            owner="kernel",
            tier="stable",
            priority=44,
            cacheable=True,
            title="Files and Artifacts",
            content=prompts.FILE_TOOL_GUIDANCE,
            guard=_has_capability("file_tools"),
        ),
        PromptBlock(
            id="kernel.format_sensitive_output",
            owner="kernel",
            tier="stable",
            priority=45,
            cacheable=True,
            title="Format-Sensitive Output",
            content=prompts.FORMAT_SENSITIVE_OUTPUT_GUIDANCE,
        ),
        PromptBlock(
            id="kernel.task_completion",
            owner="kernel",
            tier="stable",
            priority=50,
            cacheable=True,
            title="Task Completion",
            content=prompts.TASK_COMPLETION_GUIDANCE,
        ),
        PromptBlock(
            id="kernel.untrusted_context",
            owner="kernel",
            tier="stable",
            priority=70,
            cacheable=True,
            title="Untrusted Context Boundary",
            content=prompts.UNTRUSTED_CONTEXT_GUIDANCE,
        ),
        PromptBlock(
            id="kernel.skills",
            owner="kernel",
            tier="stable",
            priority=80,
            cacheable=True,
            title="Skills",
            content=prompts.SKILLS_GUIDANCE,
            guard=_has_tools("search_skills", "use_skill", "list_skills"),
        ),
        PromptBlock(
            id="kernel.skill_authoring",
            owner="kernel",
            tier="stable",
            priority=82,
            cacheable=True,
            title="Skill Authoring",
            content=prompts.SKILL_AUTHORING_GUIDANCE,
            guard=_has_tools("remember_skill"),
        ),
        PromptBlock(
            id="kernel.platform_hint",
            owner="kernel",
            tier="stable",
            priority=90,
            cacheable=True,
            title="Platform Hint",
            content=_platform_hint_content(platform),
            guard=lambda context: bool(context.platform or platform),
        ),
    ]


def build_prompt_registry(
    *,
    system_prompt: str = "",
    blocks: Iterable[PromptBlock] | None = None,
) -> PromptBlockRegistry:
    """Create a registry preserving custom prompt behavior.

    Built-in defaults are rendered as structured kernel blocks so distros and
    domains can override individual dimensions. Custom user prompts stay as one
    legacy block to avoid surprising configuration changes.
    """

    registry = PromptBlockRegistry()
    if _looks_like_default_system_prompt(system_prompt):
        registry.extend(default_system_prompt_blocks(_detect_default_platform(system_prompt)))
    elif system_prompt:
        registry.register(legacy_system_prompt_block(system_prompt))
    if blocks:
        registry.extend(blocks)
    return registry
