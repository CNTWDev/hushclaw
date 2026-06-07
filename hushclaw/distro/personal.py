"""Personal distribution — wraps current single-user local-first behavior."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.prompt_blocks import PromptBlock
from hushclaw.runtime.principal import RuntimePrincipal, SINGLE_USER_PRINCIPAL

if TYPE_CHECKING:
    from hushclaw.os_api import AgentOSService


REALITY_CALIBRATION_PROMPT = (
    "## Reality Calibration\n"
    "Before answering, silently run a brief reality calibration. Check whether the answer fits:\n"
    "- objective facts and uncertainty\n"
    "- the user's likely practical situation and real goal\n"
    "- human incentives, habits, emotions, status, trust, and friction\n"
    "- social, organizational, economic, time, and implementation constraints\n\n"
    "Use this calibration to make the answer more grounded, useful, and humane. "
    "Do not narrate the calibration or expose hidden reasoning unless the user asks for it. "
    "For simple factual or execution requests, keep the answer direct. "
    "For advice, design, product, strategy, or social questions, prefer the practical path first, "
    "then name the key tradeoffs or reality constraints when they matter. "
    "If the ideal answer and the practical answer differ, say so plainly."
)


class PersonalDistro:
    """Default local-first personal distribution.

    All new contract methods return empty/permissive values — behavior is
    identical to pre-distro HushClaw. The kernel's own defaults already
    target the personal profile.
    """

    _manifest = DistroManifest(
        id="personal",
        name="HushClaw Personal",
        description="Local-first personal AI assistant. Data stays on device.",
        storage_profile="local_sqlite",
        policy_profile="personal_owner",
        web_shell="personal",
        scope_support=["personal", "global", "workspace"],
        capabilities=[],
    )

    def manifest(self) -> DistroManifest:
        return self._manifest

    # ── Assembly-time ─────────────────────────────────────────────────────

    def agent_profile(self) -> AgentProfile:
        """No extra skills or tool restrictions — all kernel defaults apply."""
        return AgentProfile()

    def policy_rules(self) -> PolicyRuleSet:
        """Permissive — PolicyGate uses its built-in shell/fs safeguards only."""
        return PolicyRuleSet()

    def prompt_blocks(self) -> list[PromptBlock]:
        return [
            PromptBlock(
                id="personal.reality_calibration",
                owner="distro",
                tier="stable",
                priority=8,
                cacheable=True,
                title="Reality Calibration",
                content=REALITY_CALIBRATION_PROMPT,
            )
        ]

    def runtime_principal(self, **kwargs: Any) -> RuntimePrincipal:
        workspace_id = str(kwargs.get("workspace_id") or "")
        source_channel = str(kwargs.get("source_channel") or "local")
        if workspace_id or source_channel != "local":
            return RuntimePrincipal(
                workspace_id=workspace_id,
                source_channel=source_channel,
            )
        return SINGLE_USER_PRINCIPAL

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def on_startup(self, os_api: "AgentOSService") -> None:
        pass

    async def on_shutdown(self) -> None:
        pass
