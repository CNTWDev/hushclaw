from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hushclaw.config.schema import AgentConfig
from hushclaw.context.engine import DefaultContextEngine
from hushclaw.context.policy import ContextPolicy
from hushclaw.prompt_blocks import (
    PromptBlock,
    PromptBlockRegistry,
    PromptRenderContext,
    build_prompt_registry,
    default_system_prompt_blocks,
)
from hushclaw.prompts import build_system_prompt
from hushclaw.skills.prompt_blocks import build_skill_index_prompt_block


def _memory_mock() -> MagicMock:
    memory = MagicMock()
    memory.user_profile.render_profile_context = MagicMock(return_value="")
    memory.render_belief_models = MagicMock(return_value="")
    memory.load_session_working_state = MagicMock(return_value="")
    memory.recall_with_budget = MagicMock(return_value="")
    return memory


def test_prompt_block_registry_orders_filters_and_renders_callables():
    registry = PromptBlockRegistry([
        PromptBlock(id="domain.crm", owner="domain", tier="stable", priority=30, content="CRM rules"),
        PromptBlock(id="kernel.identity", owner="kernel", tier="stable", priority=10, content="Kernel"),
        PromptBlock(id="disabled", owner="distro", tier="stable", priority=1, content="no", enabled=False),
        PromptBlock(
            id="distro.mode",
            owner="distro",
            tier="stable",
            priority=20,
            content=lambda ctx: f"mode={ctx.extra['mode']}",
        ),
        PromptBlock(id="context.turn", owner="kernel", tier="context", priority=0, content="turn"),
    ])

    rendered = registry.render("stable", PromptRenderContext(extra={"mode": "enterprise"}))

    assert rendered == "Kernel\n\nmode=enterprise\n\nCRM rules"
    assert "disabled" not in rendered
    assert [item["id"] for item in registry.list_blocks(tier="stable", include_disabled=True)][0] == "disabled"
    assert registry.render("context", PromptRenderContext()) == "turn"


def test_context_engine_can_render_structured_prompt_blocks_without_domain_imports():
    registry = build_prompt_registry(
        system_prompt="You are HushClaw. Today is {date}.",
        blocks=[
            PromptBlock(
                id="enterprise.org_boundary",
                owner="distro",
                tier="stable",
                priority=20,
                content="Enterprise boundary.",
            ),
            PromptBlock(
                id="crm.operator",
                owner="domain",
                tier="stable",
                priority=50,
                content="CRM is enabled for this organization.",
            ),
        ],
    )
    engine = DefaultContextEngine(prompt_blocks=registry)
    config = AgentConfig(system_prompt="Ignored when registry is provided.", instructions="")

    stable, _dynamic = asyncio.run(engine.assemble(
        "hello",
        ContextPolicy(),
        _memory_mock(),
        config,
        session_id="s-1",
    ))

    assert "You are HushClaw." in stable
    assert "Today is {date}" not in stable
    assert "Enterprise boundary." in stable
    assert "CRM is enabled" in stable


def test_default_system_prompt_registry_uses_structured_kernel_blocks():
    registry = build_prompt_registry(system_prompt=build_system_prompt())

    block_ids = [item["id"] for item in registry.list_blocks(tier="stable")]
    rendered = registry.render("stable", PromptRenderContext())

    assert "kernel.legacy_system_prompt" not in block_ids
    assert "kernel.identity" in block_ids
    assert "kernel.task_completion" in block_ids
    assert "kernel.final_answer" in block_ids
    assert "kernel.untrusted_context" in block_ids
    assert "kernel.skills" in block_ids
    assert "## Task Completion" in rendered
    assert "## Final Answer Discipline" in rendered
    assert "## Untrusted Context Boundary" in rendered


def test_empty_system_prompt_registry_uses_structured_default_blocks():
    registry = build_prompt_registry()

    rendered = registry.render("stable", PromptRenderContext())

    assert "You are HushClaw" in rendered
    assert "## Tool Use" in rendered
    assert "## Skills" in rendered


def test_custom_system_prompt_remains_single_legacy_block():
    registry = build_prompt_registry(
        system_prompt="Custom prompt.",
        blocks=[
            PromptBlock(
                id="distro.extra",
                owner="distro",
                tier="stable",
                priority=20,
                content="Extra.",
            )
        ],
    )

    rendered = registry.render("stable", PromptRenderContext())
    block_ids = [item["id"] for item in registry.list_blocks(tier="stable")]

    assert rendered == "Custom prompt.\n\nExtra."
    assert block_ids == ["kernel.legacy_system_prompt", "distro.extra"]


def test_default_prompt_registry_renders_platform_hint_from_context():
    registry = build_prompt_registry()

    rendered = registry.render("stable", PromptRenderContext(platform="telegram"))

    assert "## Channel: Telegram" in rendered
    assert "Format for Telegram" in rendered


def test_default_prompt_registry_preserves_platform_hint_from_persisted_default():
    registry = build_prompt_registry(system_prompt=build_system_prompt("telegram"))

    rendered = registry.render("stable", PromptRenderContext())

    assert "## Channel: Telegram" in rendered
    assert "Format for Telegram" in rendered


def test_default_prompt_registry_renders_model_execution_guidance_conditionally():
    registry = build_prompt_registry()

    neutral = registry.render("stable", PromptRenderContext(model="local-small"))
    tool_capable = registry.render("stable", PromptRenderContext(model="gpt-5"))

    assert "## Model Execution Discipline" not in neutral
    assert "## Model Execution Discipline" in tool_capable
    assert "answer once" in tool_capable


def test_default_system_prompt_blocks_are_individually_addressable():
    block_ids = [block.id for block in default_system_prompt_blocks()]

    assert block_ids[:3] == [
        "kernel.identity",
        "kernel.language_policy",
        "kernel.memory",
    ]
    assert "kernel.model_execution" in block_ids


def test_static_domain_runtime_exposes_empty_prompt_blocks():
    from hushclaw.domains.base import DomainManifest, StaticDomainRuntime

    runtime = StaticDomainRuntime(DomainManifest(id="demo", name="Demo"))
    assert runtime.prompt_blocks() == []


def test_distro_runtime_registers_prompt_registry_on_agent():
    from hushclaw.distro.runtime import DistroRuntime

    class Distro:
        def manifest(self):
            return SimpleNamespace(id="test", storage_profile="local_sqlite")

        def agent_profile(self):
            return SimpleNamespace(enabled_tools=[], disabled_tools=[])

        def policy_rules(self):
            return SimpleNamespace(can_call_tool=None, can_read_memory=None, can_use_connector=None)

        def prompt_blocks(self):
            return [
                PromptBlock(
                    id="test.block",
                    owner="distro",
                    tier="stable",
                    priority=10,
                    content="Test distro block.",
                )
            ]

    class Agent:
        def __init__(self):
            self.config = SimpleNamespace(
                agent=SimpleNamespace(system_prompt="Base.", workspace_dir=None),
                tools=SimpleNamespace(enabled=[]),
            )
            self.prompt_blocks = None
            self.registry = SimpleNamespace()

        def set_prompt_blocks(self, prompt_blocks):
            self.prompt_blocks = prompt_blocks

    class Runtime(DistroRuntime):
        def __init__(self):
            self._distro = Distro()

    agent = Agent()
    runtime = Runtime()
    registry = runtime._build_prompt_registry(agent.config)
    agent.set_prompt_blocks(registry)

    rendered = agent.prompt_blocks.render("stable", PromptRenderContext())
    assert rendered == "Base.\n\nTest distro block."


def test_personal_distro_injects_reality_calibration_prompt_block():
    from hushclaw.distro.personal import PersonalDistro

    blocks = PersonalDistro().prompt_blocks()

    assert len(blocks) == 1
    block = blocks[0]
    assert block.id == "personal.reality_calibration"
    assert block.owner == "distro"
    assert block.tier == "stable"
    rendered = block.render(PromptRenderContext())
    assert "## Reality Calibration" in rendered
    assert "silently run a brief reality calibration" in rendered
    assert "Do not narrate the calibration" in rendered


def test_skill_index_prompt_block_lists_only_available_enabled_skill_metadata():
    class _Registry:
        def list_all(self):
            return [
                {"name": "deep-research", "description": "Investigate carefully.", "tier": "user", "tags": ["research"]},
                {"name": "disabled", "description": "No", "enabled": False},
                {"name": "missing-bin", "description": "No", "available": False},
            ]

    block = build_skill_index_prompt_block(_Registry())
    rendered = block.render(PromptRenderContext())

    assert "## Skill Discovery" in rendered
    assert "`deep-research` [user]: Investigate carefully. [tags: research]" in rendered
    assert "search_skills(query)" in rendered
    assert "use_skill(name)" in rendered
    assert "disabled" not in rendered
    assert "missing-bin" not in rendered


def test_skill_index_prompt_block_uses_compact_hints_for_large_skill_sets():
    class _Registry:
        def list_all(self):
            return [
                {"name": f"skill-{idx:03d}", "description": "General helper", "tier": "builtin"}
                for idx in range(80)
            ]

    block = build_skill_index_prompt_block(_Registry(), limit=60)
    rendered = block.render(PromptRenderContext())

    assert "80 enabled skills are available" in rendered
    assert rendered.count("- `skill-") == 20
    assert "more skills are searchable with `search_skills(query)`" in rendered
