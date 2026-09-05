from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hushclaw.config.schema import AgentConfig
from hushclaw.context.engine import DefaultContextEngine
from hushclaw.context.policy import ContextPolicy
from hushclaw.prompt_blocks import (
    ModelCapabilities,
    PromptAssembler,
    PromptBlock,
    PromptBlockRegistry,
    PromptRenderContext,
    build_prompt_registry,
    default_system_prompt_blocks,
    prompt_capabilities_from_tools,
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
        PromptBlock(id="context.turn", owner="kernel", tier="dynamic", priority=0, content="turn"),
    ])

    rendered = registry.render("stable", PromptRenderContext(extra={"mode": "enterprise"}))

    assert rendered == "Kernel\n\nmode=enterprise\n\nCRM rules"
    assert "disabled" not in rendered
    assert [item["id"] for item in registry.list_blocks(tier="stable", include_disabled=True)][0] == "disabled"
    assert registry.render("dynamic", PromptRenderContext()) == "turn"


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


def test_context_engine_renders_dynamic_registry_blocks_and_records_manifest():
    registry = build_prompt_registry(
        system_prompt="Custom prompt.",
        blocks=[
            PromptBlock(
                id="domain.turn_policy",
                owner="domain",
                tier="dynamic",
                priority=10,
                cacheable=False,
                content=lambda context: f"Current request: {context.query}",
            ),
        ],
    )
    engine = DefaultContextEngine(prompt_blocks=registry)
    config = AgentConfig(system_prompt="Custom prompt.", instructions="", html_render_hint=False)

    stable, dynamic = asyncio.run(engine.assemble(
        "hello dynamic",
        ContextPolicy(),
        _memory_mock(),
        config,
        session_id="s-dynamic",
    ))
    manifest = engine.context_trace()["prompt_manifest"]
    items = {item["id"]: item for item in manifest["items"]}

    assert stable == "Custom prompt."
    assert "Current request: hello dynamic" in dynamic
    assert items["domain.turn_policy"]["included"] is True
    assert items["runtime.turn_context"]["included"] is True


def test_default_system_prompt_registry_uses_structured_kernel_blocks():
    registry = build_prompt_registry(system_prompt=build_system_prompt())

    block_ids = [item["id"] for item in registry.list_blocks(tier="stable")]
    rendered = registry.render("stable", PromptRenderContext())

    assert "kernel.legacy_system_prompt" not in block_ids
    assert "kernel.identity" in block_ids
    assert "kernel.response_policy" in block_ids
    assert "kernel.format_sensitive_output" in block_ids
    assert "kernel.task_completion" in block_ids
    assert "kernel.untrusted_context" in block_ids
    assert "kernel.skills" in block_ids
    assert "## Task Completion" in rendered
    assert "## Response Policy" in rendered
    assert "## Format-Sensitive Output" in rendered
    assert "## Untrusted Context Boundary" in rendered


def test_empty_system_prompt_registry_uses_structured_default_blocks():
    registry = build_prompt_registry()

    rendered = registry.render("stable", PromptRenderContext())

    assert "You are HushClaw" in rendered
    assert "## Tool Use" not in rendered
    assert "## Skills" not in rendered


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


def test_default_prompt_registry_renders_tool_guidance_from_actual_capabilities():
    registry = build_prompt_registry()

    neutral = registry.render("stable", PromptRenderContext(model="gpt-5"))
    tool_names = frozenset({
        "research_web", "read_file",
        "search_skills", "use_skill", "list_skills",
        "remember", "recall", "session_search",
    })
    tool_capable = registry.render("stable", PromptRenderContext(
        model="local-small",
        tool_names=tool_names,
        capabilities=prompt_capabilities_from_tools(tool_names),
        model_capabilities=ModelCapabilities(tool_calls=True),
    ))

    assert "## Tool Use" not in neutral
    assert "## Memory" not in neutral
    assert "## Tool Use" in tool_capable
    assert "## Current-information research" in tool_capable
    assert "## Files and artifacts" in tool_capable
    assert "## Skills" in tool_capable


def test_prompt_does_not_describe_an_incomplete_skill_tool_contract():
    registry = build_prompt_registry()
    tool_names = frozenset({"use_skill"})

    rendered = registry.render("stable", PromptRenderContext(
        tool_names=tool_names,
        capabilities=prompt_capabilities_from_tools(tool_names),
        model_capabilities=ModelCapabilities(tool_calls=True),
    ))

    assert "## Tool Use" in rendered
    assert "## Skills" not in rendered


def test_default_system_prompt_blocks_are_individually_addressable():
    block_ids = [block.id for block in default_system_prompt_blocks()]

    assert block_ids[:4] == [
        "kernel.identity",
        "kernel.response_policy",
        "kernel.language_policy",
        "kernel.memory",
    ]
    assert "kernel.format_sensitive_output" in block_ids
    assert "kernel.web_research" in block_ids
    assert "kernel.file_tools" in block_ids


def test_prompt_assembler_exposes_content_free_manifest_and_real_dynamic_tier():
    registry = PromptBlockRegistry([
        PromptBlock(id="stable.one", content="Stable", tier="stable"),
        PromptBlock(id="dynamic.one", content="Dynamic", tier="dynamic", cacheable=False),
        PromptBlock(
            id="guarded",
            content="Hidden",
            tier="stable",
            guard=lambda context: "enabled" in context.capabilities,
        ),
    ])

    assembly = PromptAssembler(registry).assemble(PromptRenderContext())
    manifest = assembly.manifest_dict()
    items = {item["id"]: item for item in manifest["items"]}

    assert assembly.stable == "Stable"
    assert assembly.dynamic == "Dynamic"
    assert items["stable.one"]["included"] is True
    assert items["stable.one"]["content_hash"]
    assert items["dynamic.one"]["cacheable"] is False
    assert items["guarded"]["reason"] == "guard_false"
    assert "Stable" not in str(manifest)


def test_response_policy_can_be_overridden_without_changing_kernel_assembly():
    registry = build_prompt_registry(
        blocks=[
            PromptBlock(
                id="kernel.response_policy",
                owner="user",
                tier="stable",
                priority=5,
                content="Custom response policy.",
            )
        ]
    )

    rendered = registry.render("stable", PromptRenderContext())

    assert "Custom response policy." in rendered
    assert "## Response Policy" not in rendered


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
    rendered = block.render(PromptRenderContext(
        tool_names=frozenset({"search_skills", "use_skill", "list_skills"}),
        capabilities=frozenset({"skill_tools"}),
        model_capabilities=ModelCapabilities(tool_calls=True),
    ))

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
    rendered = block.render(PromptRenderContext(
        tool_names=frozenset({"search_skills", "use_skill", "list_skills"}),
        capabilities=frozenset({"skill_tools"}),
        model_capabilities=ModelCapabilities(tool_calls=True),
    ))

    assert "80 enabled skills are available" in rendered
    assert rendered.count("- `skill-") == 20
    assert "more skills are searchable with `search_skills(query)`" in rendered
