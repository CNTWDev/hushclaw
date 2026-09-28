"""Replay personal regression cases and grade the answers.

Each case is answered in a throwaway ``eval:`` session that is deleted
afterwards, so replays never enter memory, session search or learning.
Replays run under the ``eval`` channel with read-only tools only: nothing that
writes files, memory, calendars or runs shell commands.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from hushclaw.learning.controller import EVAL_SESSION_PREFIX
from hushclaw.prompts import EVAL_JUDGE_SYSTEM, EVAL_JUDGE_USER_TEMPLATE
from hushclaw.providers.base import Message
from hushclaw.util.ids import make_id

AnswerFn = Callable[[dict], Awaitable[str]]
JudgeFn = Callable[[dict, str], Awaitable[tuple[bool, str]]]


@dataclass(slots=True)
class EvalCaseResult:
    case_id: str
    kind: str
    passed: bool
    reason: str
    response: str = ""


@dataclass(slots=True)
class EvalReport:
    batch_id: str
    results: list[EvalCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "passed": self.passed,
            "total": self.total,
            "results": [
                {"case_id": r.case_id, "kind": r.kind, "passed": r.passed, "reason": r.reason}
                for r in self.results
            ],
        }


async def run_eval_cases(memory, cases: list[dict], *, answer_fn: AnswerFn, judge_fn: JudgeFn) -> EvalReport:
    report = EvalReport(batch_id=make_id("evalb-"))
    for case in cases:
        try:
            response = await answer_fn(case)
            passed, reason = await judge_fn(case, response)
        except Exception as exc:
            response, passed, reason = "", False, f"error: {type(exc).__name__}: {exc}"[:300]
        memory.eval_cases.record_run(
            case["case_id"], passed=passed, reason=reason, response=response, batch_id=report.batch_id,
        )
        report.results.append(EvalCaseResult(case["case_id"], case["kind"], passed, reason, response))
    return report


def llm_judge(provider, model: str) -> JudgeFn:
    async def judge(case: dict, response: str) -> tuple[bool, str]:
        prompt = EVAL_JUDGE_USER_TEMPLATE.format(
            kind=case.get("kind", ""),
            prompt=str(case.get("prompt") or "")[:2000],
            expectation=str(case.get("expectation") or "")[:1000],
            previous_response=str(case.get("response") or "")[:1500],
            response=str(response or "")[:3000],
        )
        resp = await provider.complete(
            messages=[Message(role="user", content=prompt)],
            system=EVAL_JUDGE_SYSTEM,
            max_tokens=200,
            model=model,
        )
        text = getattr(resp, "content", "") or ""
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return False, "judge returned no verdict"
        obj = json.loads(text[start:end + 1])
        return bool(obj.get("passed")), str(obj.get("reason") or "")[:300]

    return judge


def read_only_tool_names(registry) -> list[str]:
    names = []
    for td in registry.list_tools():
        if getattr(td, "parallel_safe", False) and not getattr(td, "mutating", False):
            names.append(td.name)
    return sorted(set(names))


def agent_answer_fn(agent, gateway=None) -> AnswerFn:
    from hushclaw.runtime.principal import RuntimePrincipal, principal_context  # noqa: PLC0415

    async def answer(case: dict) -> str:
        session_id = f"{EVAL_SESSION_PREFIX}{case['case_id'][-12:]}:{int(time.time() * 1000)}"
        principal = RuntimePrincipal(source_channel="eval")
        try:
            with principal_context(principal):
                loop = agent.new_loop(session_id=session_id, gateway=gateway)
                return await loop.run(case["prompt"])
        finally:
            try:
                agent.memory.delete_session(session_id)
            except Exception:
                pass

    return answer
