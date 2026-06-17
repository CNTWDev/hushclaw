"""Structured prompt block contract for AgentOS.

AgentOS owns the renderer and ordering rules. Distros and domains may declare
additional blocks through this narrow contract, but the kernel never imports
business modules to discover them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

PromptTier = Literal["stable", "context", "volatile", "ephemeral"]
PromptOwner = Literal["kernel", "distro", "domain", "user"]
PromptContent = str | Callable[["PromptRenderContext"], str]

_VALID_TIERS = {"stable", "context", "volatile", "ephemeral"}
_VALID_OWNERS = {"kernel", "distro", "domain", "user"}


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
    extra: Mapping[str, Any] = field(default_factory=dict)


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
        if not self.enabled:
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
        cacheable: bool | None = None,
        include_disabled: bool = False,
    ) -> list[PromptBlock]:
        items = list(self._blocks.values())
        if tier is not None:
            items = [block for block in items if block.tier == tier]
        if owner is not None:
            items = [block for block in items if block.owner == owner]
        if cacheable is not None:
            items = [block for block in items if block.cacheable is cacheable]
        if not include_disabled:
            items = [block for block in items if block.enabled]
        return sorted(items, key=lambda block: (block.priority, block.owner, block.id))

    def render(
        self,
        tier: PromptTier,
        context: PromptRenderContext,
        *,
        cacheable: bool | None = None,
    ) -> str:
        rendered = [
            text
            for block in self.blocks(tier=tier, cacheable=cacheable)
            if (text := block.render(context))
        ]
        return "\n\n".join(rendered)

    def render_tiers(
        self,
        tiers: Iterable[PromptTier],
        context: PromptRenderContext,
        *,
        cacheable: bool | None = None,
    ) -> str:
        rendered = [self.render(tier, context, cacheable=cacheable) for tier in tiers]
        return "\n\n".join(text for text in rendered if text)

    def list_blocks(
        self,
        *,
        tier: PromptTier | None = None,
        owner: PromptOwner | None = None,
        cacheable: bool | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            block.metadata()
            for block in self.blocks(
                tier=tier,
                owner=owner,
                cacheable=cacheable,
                include_disabled=include_disabled,
            )
        ]

    def copy(self) -> "PromptBlockRegistry":
        return PromptBlockRegistry(self._blocks.values())


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


def _model_execution_content(context: PromptRenderContext) -> str:
    from hushclaw.prompts import MODEL_EXECUTION_GUIDANCE

    model = (context.model or "").lower()
    if not model:
        return ""
    tool_capable_markers = (
        "gpt",
        "codex",
        "o3",
        "o4",
        "gemini",
        "gemma",
        "grok",
        "claude",
        "qwen",
        "deepseek",
        "kimi",
    )
    if any(marker in model for marker in tool_capable_markers):
        return MODEL_EXECUTION_GUIDANCE
    return ""


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
            id="kernel.final_answer",
            owner="kernel",
            tier="stable",
            priority=60,
            cacheable=True,
            title="Final Answer Discipline",
            content=prompts.FINAL_ANSWER_DISCIPLINE,
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
        ),
        PromptBlock(
            id="kernel.platform_hint",
            owner="kernel",
            tier="stable",
            priority=90,
            cacheable=False,
            title="Platform Hint",
            content=_platform_hint_content(platform),
        ),
        PromptBlock(
            id="kernel.model_execution",
            owner="kernel",
            tier="stable",
            priority=95,
            cacheable=False,
            title="Model Execution Discipline",
            content=_model_execution_content,
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
