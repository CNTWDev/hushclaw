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
        rendered = [
            text
            for block in self.blocks(tier=tier)
            if (text := block.render(context))
        ]
        return "\n\n".join(rendered)

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


def build_prompt_registry(
    *,
    system_prompt: str = "",
    blocks: Iterable[PromptBlock] | None = None,
) -> PromptBlockRegistry:
    """Create a registry preserving the existing system prompt behavior."""

    registry = PromptBlockRegistry()
    if system_prompt:
        registry.register(legacy_system_prompt_block(system_prompt))
    if blocks:
        registry.extend(blocks)
    return registry
