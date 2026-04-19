"""Lifecycle-driven learning controller."""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

from hushclaw.learning.fingerprint import fingerprint_task
from hushclaw.learning.reflection import TaskTrace, extract_profile_updates, reflect_trace
from hushclaw.prompts import (
    BELIEF_MODEL_CONSOLIDATION_SYSTEM,
    BELIEF_MODEL_CONSOLIDATION_TEMPLATE,
    PROFILE_EXTRACTION_SYSTEM,
    PROFILE_EXTRACTION_USER_TEMPLATE,
    AUTO_EXTRACT_SYSTEM,
    AUTO_EXTRACT_USER_TEMPLATE,
    REFLECT_SYSTEM,
    REFLECT_USER_TEMPLATE,
)
from hushclaw.providers.base import Message
from hushclaw.util.logging import get_logger

log = get_logger("learning")
_BELIEF_CONSOLIDATION_MIN_INTERVAL = 45.0


class LearningController:
    """Collect per-turn traces from hooks and persist lightweight learning artifacts."""

    def __init__(self, memory, skill_manager=None, provider=None, agent_config=None) -> None:
        self.memory = memory
        self.skill_manager = skill_manager
        self.provider = provider
        self.agent_config = agent_config
        self._pending: dict[str, dict] = defaultdict(lambda: {
            "tool_trace": [],
            "errors": [],
            "used_skills": [],
            "corrections": [],
        })
        self._belief_jobs_in_flight: set[tuple[str, ...]] = set()
        self._belief_last_attempt_at: dict[tuple[str, ...], float] = {}

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
        workspace = str(payload.get("workspace") or "")
        asyncio.create_task(self._maybe_consolidate_belief_models(
            session_id=session_id,
            user_input=user_input,
            workspace=workspace,
        ))
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
            # Profile extraction + fact extraction — background, non-blocking
            asyncio.create_task(self._run_all_learning(trace))
            return
        asyncio.create_task(self._run_all_learning(trace, do_reflect=True))

    async def _run_all_learning(self, trace: TaskTrace, *, do_reflect: bool = False) -> None:
        """Single async task that runs all post-turn learning. Best-effort."""
        cheap_model = getattr(self.agent_config, "cheap_model", "") if self.agent_config else ""
        use_llm = bool(cheap_model and self.provider is not None)

        # 1. Profile fact extraction (user profile dimensions)
        if use_llm:
            profile_updates = await self._extract_profile_llm(trace, cheap_model)
        else:
            profile_updates = extract_profile_updates(trace)

        # 2. Semantic fact extraction into knowledge base (interests/beliefs/decisions)
        if use_llm:
            asyncio.create_task(self._extract_facts_llm(trace, cheap_model))

        # 3. Reflection (only when should_reflect gated)
        if do_reflect:
            if use_llm:
                result = await self._reflect_llm(trace, cheap_model)
            else:
                result = reflect_trace(trace)
            await self._persist_reflection(trace, result, profile_updates)
        elif profile_updates:
            await self._persist_profile_updates(trace.session_id, profile_updates)

    async def _extract_profile_llm(self, trace: TaskTrace, model: str) -> list[dict]:
        """Call cheap_model to extract structured user profile facts. Returns [] on failure."""
        user_input = (trace.user_input or "").strip()
        if len(user_input) < 10:
            return []
        prompt = PROFILE_EXTRACTION_USER_TEMPLATE.format(user_input=user_input[:600])
        try:
            resp = await self.provider.complete(
                messages=[Message(role="user", content=prompt)],
                system=PROFILE_EXTRACTION_SYSTEM,
                max_tokens=600,
                model=model,
            )
            content = (resp.content or "").strip()
            start = content.find("[")
            end = content.rfind("]")
            if start < 0 or end <= start:
                return []
            items = json.loads(content[start:end + 1])
            if not isinstance(items, list):
                return []
            valid: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                cat = str(item.get("category") or "").strip()
                key = str(item.get("key") or "").strip()
                val = item.get("value")
                conf = float(item.get("confidence") or 0.5)
                if cat and key and isinstance(val, dict):
                    valid.append({"category": cat, "key": key, "value": val, "confidence": min(1.0, max(0.0, conf))})
            log.debug("llm profile extraction: %d facts model=%s", len(valid), model)
            return valid
        except Exception as e:
            log.debug("llm profile extraction failed (%s), falling back to rules", e)
            return extract_profile_updates(trace)

    async def _extract_facts_llm(self, trace: TaskTrace, model: str) -> None:
        """Call cheap_model to extract durable knowledge facts and save to memory store."""
        user_input = (trace.user_input or "").strip()
        assistant_response = (trace.assistant_response or "").strip()
        if len(user_input) < 15:
            return
        prompt = AUTO_EXTRACT_USER_TEMPLATE.format(
            user_input=user_input[:500],
            assistant_response=assistant_response[:300],
        )
        try:
            resp = await self.provider.complete(
                messages=[Message(role="user", content=prompt)],
                system=AUTO_EXTRACT_SYSTEM,
                max_tokens=700,
                model=model,
            )
            content = (resp.content or "").strip()
            start = content.find("[")
            end = content.rfind("]")
            if start < 0 or end <= start:
                return
            items = json.loads(content[start:end + 1])
            if not isinstance(items, list):
                return
            saved = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                body = str(item.get("body") or "").strip()
                title = str(item.get("title") or "").strip()[:80]
                note_type = str(item.get("note_type") or "fact").strip()
                tags = list(item.get("tags") or [])
                if not body or len(body) < 10 or not title:
                    continue
                if note_type not in {"interest", "belief", "preference", "decision", "fact"}:
                    note_type = "fact"
                tags = [str(t) for t in tags if isinstance(t, str)][:3]
                tags.append("_auto_extract")
                try:
                    if not self.memory.note_exists_with_title(title):
                        self.memory.remember(
                            body,
                            title=title,
                            tags=tags,
                            note_type=note_type,
                            persist_to_disk=False,
                        )
                        saved += 1
                except Exception:
                    pass
            log.debug("llm fact extraction: %d notes saved model=%s", saved, model)
        except Exception as e:
            log.debug("llm fact extraction failed: %s", e)

    async def _reflect_llm(self, trace: TaskTrace, model: str):
        """Call cheap_model to produce structured reflection. Falls back to reflect_trace()."""
        from hushclaw.learning.reflection import ReflectionResult
        try:
            tool_sequence = " → ".join(
                str(t.get("tool_name") or "") for t in trace.tool_trace[:8] if t.get("tool_name")
            ) or "none"
            prompt = REFLECT_USER_TEMPLATE.format(
                task_fingerprint=trace.task_fingerprint or "unknown",
                user_input=(trace.user_input or "")[:300],
                tool_sequence=tool_sequence,
                errors="; ".join(trace.errors[:2]) or "none",
                corrections="; ".join(trace.corrections[:1]) or "none",
                used_skills=", ".join(trace.used_skills) or "none",
                outcome_preview=(trace.assistant_response or "")[:200],
            )
            resp = await self.provider.complete(
                messages=[Message(role="user", content=prompt)],
                system=REFLECT_SYSTEM,
                max_tokens=400,
                model=model,
            )
            content = (resp.content or "").strip()
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("no JSON object in response")
            obj = json.loads(content[start:end + 1])
            result = ReflectionResult(
                success=bool(obj.get("success", not trace.errors)),
                outcome=str(obj.get("outcome") or "")[:260],
                failure_mode=str(obj.get("failure_mode") or "")[:200],
                lesson=str(obj.get("lesson") or "")[:280],
                strategy_hint=str(obj.get("strategy_hint") or "")[:280],
                profile_updates=[],
            )
            log.debug("llm reflection done model=%s success=%s", model, result.success)
            return result
        except Exception as e:
            log.debug("llm reflection failed (%s), falling back to rules", e)
            return reflect_trace(trace)

    async def _persist_profile_updates(self, session_id: str, updates: list[dict]) -> None:
        """Persist profile fact updates. Best-effort — failures are logged and swallowed."""
        try:
            for update in updates:
                self.memory.user_profile.upsert_fact(
                    category=str(update.get("category") or "preferences"),
                    key=str(update.get("key") or "fact"),
                    value=update.get("value") or {},
                    confidence=float(update.get("confidence") or 0.5),
                    source_session_id=session_id,
                )
        except Exception as e:
            log.warning("profile update persist failed: %s", e)

    async def _persist_reflection(self, trace: TaskTrace, result, profile_updates: list[dict] | None = None) -> None:
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
            for update in (profile_updates or []):
                self.memory.user_profile.upsert_fact(
                    category=str(update.get("category") or "preferences"),
                    key=str(update.get("key") or "fact"),
                    value=update.get("value") or {},
                    confidence=float(update.get("confidence") or 0.5),
                    source_session_id=trace.session_id,
                )
            # Derive quality score from execution signals:
            #   corrections (user said "not what I asked") → 0.0
            #   errors during execution                    → 0.6
            #   clean run                                  → 1.0
            quality_score = 0.0 if trace.corrections else (0.6 if trace.errors else 1.0)
            for skill_name in trace.used_skills:
                self.memory.record_skill_outcome(
                    skill_name=skill_name,
                    session_id=trace.session_id,
                    task_fingerprint=trace.task_fingerprint,
                    success=result.success,
                    note=result.lesson,
                    quality_score=quality_score,
                )
            await self._maybe_auto_patch_skill(trace, result)
        except Exception as e:
            log.warning("reflection persist failed: %s", e)

    async def _maybe_consolidate_belief_models(
        self,
        *,
        session_id: str,
        user_input: str,
        workspace: str = "",
    ) -> None:
        """Asynchronously batch-refine dirty belief models using the configured LLM."""
        if self.provider is None or self.agent_config is None:
            return

        scopes: list[str] = ["global"]
        ms = getattr(self.agent_config, "memory_scope", "") or ""
        if ms:
            scopes.append(f"agent:{ms}")
        if workspace:
            scopes.append(f"workspace:{workspace}")
        scope_key = tuple(sorted(set(scopes)))
        if scope_key in self._belief_jobs_in_flight:
            return
        now = time.time()
        last_attempt = self._belief_last_attempt_at.get(scope_key, 0.0)
        if now - last_attempt < _BELIEF_CONSOLIDATION_MIN_INTERVAL:
            return

        dirty_models = self.memory.list_dirty_belief_models(scopes=list(scope_key), limit=3)
        if not dirty_models:
            return

        self._belief_jobs_in_flight.add(scope_key)
        self._belief_last_attempt_at[scope_key] = now
        try:
            payload_models = []
            for model in dirty_models:
                payload_models.append({
                    "domain": model["domain"],
                    "scope": model["scope"],
                    "latest": model["latest"],
                    "entries": [
                        {
                            "note_type": str(e.get("note_type") or ""),
                            "content": str(e.get("content") or "")[:220],
                        }
                        for e in (model.get("entries") or [])[:6]
                    ],
                })

            prompt = (
                f"{BELIEF_MODEL_CONSOLIDATION_TEMPLATE}\n\n"
                f"Current user query:\n{user_input[:220]}\n\n"
                "Buckets:\n"
                f"{json.dumps(payload_models, ensure_ascii=False, indent=2)}"
            )
            model_name = getattr(self.agent_config, "cheap_model", "") or getattr(self.agent_config, "model", None)
            resp = await self.provider.complete(
                messages=[Message(role="user", content=prompt)],
                system=BELIEF_MODEL_CONSOLIDATION_SYSTEM,
                max_tokens=900,
                model=model_name,
            )
            content = (resp.content or "").strip()
            start = content.find("[")
            end = content.rfind("]")
            if start >= 0 and end > start:
                content = content[start:end + 1]
            items = json.loads(content)
            if not isinstance(items, list):
                return
            applied = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                domain = str(item.get("domain") or "").strip()
                scope = str(item.get("scope") or "").strip()
                if not domain or not scope:
                    continue
                self.memory.save_belief_model_consolidation(
                    domain=domain,
                    scope=scope,
                    summary=str(item.get("summary") or ""),
                    trajectory=str(item.get("trajectory") or ""),
                    signals=list(item.get("signals") or []),
                )
                applied += 1
            if applied:
                log.info(
                    "belief consolidation updated %d model(s) for session=%s scopes=%s",
                    applied,
                    session_id[:8],
                    ",".join(scope_key),
                )
        except Exception as e:
            log.debug("belief consolidation skipped: %s", e)
        finally:
            self._belief_jobs_in_flight.discard(scope_key)

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
