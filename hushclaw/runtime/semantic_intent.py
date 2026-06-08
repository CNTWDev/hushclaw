"""LLM-backed semantic intent classification for control-flow decisions."""
from __future__ import annotations

import json
from dataclasses import dataclass

from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger

log = get_logger("runtime.semantic_intent")


@dataclass(slots=True)
class PendingActionDecision:
    action: str = "unclear"  # confirm | modify | cancel | unclear
    replacement_text: str = ""
    reason: str = ""


PENDING_ACTION_SYSTEM = (
    "Classify the user's reply to a pending external action confirmation.\n"
    "Return JSON only: {\"action\":\"confirm|modify|cancel|unclear\","
    "\"replacement_text\":\"\",\"reason\":\"...\"}.\n"
    "Rules:\n"
    "- confirm: user clearly approves executing the pending action as shown.\n"
    "- modify: user asks to change the pending content before execution; include the new text if present.\n"
    "- cancel: user clearly rejects, cancels, or says not to execute.\n"
    "- unclear: anything ambiguous, conversational, or not about executing the pending action.\n"
    "Do not infer confirmation from ordinary discussion."
)


class SemanticIntentService:
    def __init__(self, provider=None, model: str = "", max_tokens: int = 200) -> None:
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens

    async def classify_pending_action(
        self,
        *,
        user_input: str,
        pending_action_summary: str,
    ) -> PendingActionDecision:
        if self.provider is None or not self.model:
            return PendingActionDecision(reason="semantic intent provider unavailable")
        prompt = (
            "Pending action:\n"
            f"{pending_action_summary[:1200]}\n\n"
            "User reply:\n"
            f"{(user_input or '').strip()[:800]}\n\n"
            "Classify the reply."
        )
        try:
            resp = await self.provider.complete(
                messages=[Message(role="user", content=prompt)],
                system=PENDING_ACTION_SYSTEM,
                max_tokens=self.max_tokens,
                model=self.model,
            )
            content = (resp.content or "").strip()
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end + 1]
            data = json.loads(content)
            if not isinstance(data, dict):
                return PendingActionDecision(reason="non-object classifier response")
            action = str(data.get("action") or "unclear").strip().lower()
            if action not in {"confirm", "modify", "cancel", "unclear"}:
                action = "unclear"
            return PendingActionDecision(
                action=action,
                replacement_text=str(data.get("replacement_text") or "").strip(),
                reason=str(data.get("reason") or "").strip()[:240],
            )
        except Exception as exc:
            log.debug("pending action semantic classification failed: %s", exc)
            return PendingActionDecision(reason=f"classification failed: {exc}")
