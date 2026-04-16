"""Lifecycle-driven learning controller."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from hushclaw.learning.fingerprint import fingerprint_task
from hushclaw.learning.reflection import TaskTrace, reflect_trace
from hushclaw.util.logging import get_logger

log = get_logger("learning")


class LearningController:
    """Collect per-turn traces from hooks and persist lightweight learning artifacts."""

    def __init__(self, memory, skill_manager=None) -> None:
        self.memory = memory
        self.skill_manager = skill_manager
        self._pending: dict[str, dict] = defaultdict(lambda: {
            "tool_trace": [],
            "errors": [],
            "used_skills": [],
            "corrections": [],
        })

    def on_pre_session_init(self, event) -> None:
        session_id = str(event.payload.get("session_id") or "")
        if not session_id:
            return
        self._pending[session_id] = {
            "tool_trace": [],
            "errors": [],
            "used_skills": [],
            "corrections": [],
        }

    def on_post_tool_call(self, event) -> None:
        payload = event.payload
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        tool_name = str(payload.get("tool_name") or "")
        entry = {
            "tool_name": tool_name,
            "tool_input": payload.get("tool_input") or {},
            "tool_result": str(payload.get("tool_result") or "")[:400],
            "is_error": bool(payload.get("is_error")),
        }
        trace = self._pending[session_id]
        trace["tool_trace"].append(entry)
        if entry["is_error"]:
            trace["errors"].append(f"{tool_name}: {entry['tool_result']}")
        if tool_name == "use_skill":
            name = str((payload.get("tool_input") or {}).get("name") or "").strip()
            if name and name not in trace["used_skills"]:
                trace["used_skills"].append(name)

    async def on_post_turn_persist(self, event) -> None:
        payload = event.payload
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        user_input = str(payload.get("user_input") or "")
        assistant_response = str(payload.get("assistant_response") or "")
        lower = user_input.lower()
        # Pop _pending synchronously — this must happen before any next-turn
        # pre_session_init can reset it (which would cause a data loss race).
        trace_state = self._pending.pop(session_id, {
            "tool_trace": [],
            "errors": [],
            "used_skills": [],
            "corrections": [],
        })
        if any(tok in lower for tok in ("not what i asked", "不是这个", "不对", "不需要", "太长", "太啰嗦")):
            trace_state["corrections"].append(user_input[:200])
        task_fp = fingerprint_task(
            user_input,
            [item.get("tool_name", "") for item in trace_state["tool_trace"]],
        )
        trace = TaskTrace(
            session_id=session_id,
            user_input=user_input,
            assistant_response=assistant_response,
            tool_trace=list(trace_state["tool_trace"]),
            errors=list(trace_state["errors"]),
            corrections=list(trace_state["corrections"]),
            used_skills=list(trace_state["used_skills"]),
            workspace=str(payload.get("workspace") or ""),
            turn_count=1,
            task_fingerprint=task_fp,
        )
        if not self.should_reflect(trace):
            return
        result = reflect_trace(trace)
        # Schedule SQLite writes in the background — data is already captured above,
        # so there is no race with the next turn's pre_session_init.
        asyncio.create_task(self._persist_reflection(trace, result))

    async def _persist_reflection(self, trace: TaskTrace, result) -> None:
        """Write reflection results to persistent storage.  Best-effort — failures
        are logged and swallowed so they never surface to the user."""
        try:
            self.memory.record_reflection(
                session_id=trace.session_id,
                task_fingerprint=trace.task_fingerprint,
                success=result.success,
                outcome=result.outcome,
                failure_mode=result.failure_mode,
                lesson=result.lesson,
                strategy_hint=result.strategy_hint,
                skill_name=(trace.used_skills[0] if trace.used_skills else ""),
                source_turn_count=trace.turn_count,
            )
            for update in result.profile_updates:
                self.memory.user_profile.upsert_fact(
                    category=str(update.get("category") or "preferences"),
                    key=str(update.get("key") or "fact"),
                    value=update.get("value") or {},
                    confidence=float(update.get("confidence") or 0.5),
                    source_session_id=trace.session_id,
                )
            for skill_name in trace.used_skills:
                self.memory.record_skill_outcome(
                    skill_name=skill_name,
                    session_id=trace.session_id,
                    task_fingerprint=trace.task_fingerprint,
                    success=result.success,
                    note=result.lesson,
                )
            await self._maybe_auto_patch_skill(trace, result)
        except Exception as e:
            log.warning("reflection persist failed: %s", e)

    @staticmethod
    def should_reflect(trace: TaskTrace) -> bool:
        return bool(
            len(trace.tool_trace) >= 3
            or trace.errors
            or trace.corrections
            or trace.used_skills
        )

    async def _maybe_auto_patch_skill(self, trace: TaskTrace, result) -> None:
        """Conservative auto-patch: only refine a single editable skill on strong signals."""
        if self.skill_manager is None or len(trace.used_skills) != 1:
            return
        skill_name = trace.used_skills[0]
        skill = self.skill_manager.get(skill_name)
        if not skill or skill.get("tier") == "builtin":
            return
        current_content = str(skill.get("content") or "")

        patch_text = ""
        if trace.corrections or trace.errors:
            patch_text = (
                f"Refinement from execution feedback: {result.lesson} "
                f"Avoid failure mode: {result.failure_mode or 'user correction signal'}."
            ).strip()
        elif result.success:
            recent = self.memory.list_skill_outcomes(skill_name, limit=3)
            same_fp = [r for r in recent if (r.get("task_fingerprint") or "") == trace.task_fingerprint]
            if len(same_fp) >= 3 and all(int(r.get("success") or 0) == 1 for r in same_fp[:3]):
                patch_text = (
                    f"Validated workflow for {trace.task_fingerprint}: "
                    f"{result.strategy_hint} Preserve this flow for similar future tasks."
                )

        if not patch_text:
            return
        if patch_text in current_content:
            return
        try:
            self.skill_manager.patch(skill_name, patch_text)
        except Exception:
            pass
