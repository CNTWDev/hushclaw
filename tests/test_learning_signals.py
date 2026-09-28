"""Correction signal, feedback-to-learning bridge, and personal eval cases."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from hushclaw.learning.controller import LearningController
from hushclaw.learning.corrections import looks_like_correction, strong_correction
from hushclaw.learning.eval_runner import run_eval_cases
from hushclaw.learning.feedback_bridge import apply_message_feedback
from hushclaw.learning.reflection import TaskTrace
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.base import LLMResponse


@pytest.fixture
def memory(tmp_path):
    m = MemoryStore(tmp_path)
    yield m
    m.close()


def user_msg(memory, text, sid="s"):
    return "event:" + memory.session_log.append(sid, "user_message_received", {"input": text})


def assistant_msg(memory, text, sid="s"):
    return "event:" + memory.session_log.append(sid, "assistant_message_emitted", {"text": text})


class ScriptedProvider:
    """Answers the correction classifier; returns empty JSON for other learning prompts."""

    def __init__(self, verdict: dict):
        self.verdict = verdict
        self.systems: list[str] = []

    async def complete(self, messages, system="", max_tokens=0, model=""):
        self.systems.append(system)
        if "corrects the assistant's previous answer" in system:
            return LLMResponse(content=json.dumps(self.verdict, ensure_ascii=False), stop_reason="end_turn")
        return LLMResponse(content="[]", stop_reason="end_turn")


def controller(memory, provider=None):
    cfg = SimpleNamespace(cheap_model="cheap", model="main", memory_scope="")
    return LearningController(memory, provider=provider, agent_config=cfg)


def persist(ctl, memory, *, sid, user_text, answer, skills=()):
    uid = user_msg(memory, user_text, sid)
    aid = assistant_msg(memory, answer, sid)
    for skill in skills:
        ctl._pending[sid]["used_skills"].append(skill)
    event = SimpleNamespace(payload={
        "session_id": sid, "user_input": user_text, "assistant_response": answer,
        "user_message_id": uid, "assistant_message_id": aid,
    })

    async def run():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ctl, "_maybe_consolidate_belief_models", lambda **kw: asyncio.sleep(0))
            await ctl.on_post_turn_persist(event)

    asyncio.run(run())
    row = memory.conn.execute("SELECT payload FROM learning_jobs WHERE job_id=?", (uid,)).fetchone()
    return TaskTrace(**json.loads(row[0])["trace"]), uid, aid


def test_correction_cues_cover_chinese_and_english():
    assert looks_like_correction("不对，我说的是明年的预算")
    assert looks_like_correction("No, I meant the other file")
    assert strong_correction("你理解错了")
    assert not looks_like_correction("谢谢，再帮我查一下天气")
    assert not strong_correction("可以再详细一点吗")


def test_correction_is_attributed_to_previous_answer(memory):
    provider = ScriptedProvider({"is_correction": True, "what_was_wrong": "用了今年的数据", "expected": "使用 2027 年预算"})
    ctl = controller(memory, provider)
    persist(ctl, memory, sid="s", user_text="帮我总结明年的预算", answer="今年预算是 100 万", skills=["budget-skill"])
    trace, uid, _ = persist(ctl, memory, sid="s", user_text="不对，我说的是明年的预算", answer="明年预算是 120 万")
    assert trace.previous_turn["used_skills"] == ["budget-skill"]

    asyncio.run(ctl._run_all_learning(trace))

    refl = memory.conn.execute("SELECT * FROM reflections WHERE source_message_id=?", (uid,)).fetchone()
    assert refl is not None and refl["success"] == 0 and refl["failure_mode"] == "用了今年的数据"
    outcome = memory.conn.execute("SELECT quality_score, success FROM skill_outcomes WHERE skill_name='budget-skill'").fetchone()
    assert outcome["quality_score"] == 0.0 and outcome["success"] == 0
    [case] = memory.eval_cases.list(kind="correction")
    assert case["prompt"] == "帮我总结明年的预算"
    assert case["response"] == "今年预算是 100 万"
    assert "2027" in case["expectation"]


def test_classifier_can_reject_a_false_positive(memory):
    provider = ScriptedProvider({"is_correction": False, "what_was_wrong": "", "expected": ""})
    ctl = controller(memory, provider)
    persist(ctl, memory, sid="s", user_text="列出三个方案", answer="A、B、C")
    trace, _, _ = persist(ctl, memory, sid="s", user_text="不是很懂 B，能展开吗", answer="B 是……")
    asyncio.run(ctl._run_all_learning(trace))
    assert memory.eval_cases.list() == []
    assert memory.conn.execute("SELECT count(*) FROM reflections").fetchone()[0] == 0


def test_plain_follow_up_never_calls_the_classifier(memory):
    provider = ScriptedProvider({"is_correction": True})
    ctl = controller(memory, provider)
    persist(ctl, memory, sid="s", user_text="写一首诗", answer="……")
    trace, _, _ = persist(ctl, memory, sid="s", user_text="再写一首关于海的", answer="……")
    asyncio.run(ctl._run_all_learning(trace))
    assert not any("corrects the assistant" in s for s in provider.systems)


def test_without_llm_only_strong_cues_count(memory):
    ctl = controller(memory, provider=None)
    persist(ctl, memory, sid="s", user_text="翻译这段话", answer="Hello")
    weak, _, _ = persist(ctl, memory, sid="s", user_text="重新来一版更口语的", answer="Hi")
    asyncio.run(ctl._run_all_learning(weak))
    assert memory.eval_cases.list() == []
    strong, _, _ = persist(ctl, memory, sid="s", user_text="你理解错了，我要日文", answer="こんにちは")
    asyncio.run(ctl._run_all_learning(strong))
    assert len(memory.eval_cases.list(kind="correction")) == 1


def test_deleting_the_correction_message_removes_derived_learning(memory):
    provider = ScriptedProvider({"is_correction": True, "what_was_wrong": "x", "expected": "y"})
    ctl = controller(memory, provider)
    persist(ctl, memory, sid="s", user_text="问题", answer="答案")
    trace, uid, _ = persist(ctl, memory, sid="s", user_text="错了", answer="新答案")
    asyncio.run(ctl._run_all_learning(trace))
    assert memory.eval_cases.list()
    memory.delete_message_derived_data(uid)
    assert memory.eval_cases.list(status="") == []
    assert memory.conn.execute("SELECT count(*) FROM reflections WHERE source_message_id=?", (uid,)).fetchone()[0] == 0


def test_eval_sessions_do_not_feed_learning(memory):
    ctl = controller(memory)
    ctl._pending["eval:x"]["used_skills"].append("s")
    event = SimpleNamespace(payload={"session_id": "eval:x", "user_input": "q", "assistant_response": "a"})
    asyncio.run(ctl.on_post_turn_persist(event))
    assert memory.conn.execute("SELECT count(*) FROM learning_jobs").fetchone()[0] == 0


def test_reflection_notes_keep_new_lessons(memory):
    ctl = controller(memory)
    ctl._append_reflection_note("Reflection: fp", "Lesson: one", "")
    ctl._append_reflection_note("Reflection: fp", "Lesson: two", "")
    ctl._append_reflection_note("Reflection: fp", "Lesson: two", "")
    bodies = [r[0] for r in memory.conn.execute(
        "SELECT b.body FROM notes n JOIN note_bodies b USING(note_id) WHERE n.title='Reflection: fp'")]
    assert sorted(bodies) == ["Lesson: one", "Lesson: two"]


# ── Explicit feedback ────────────────────────────────────────────────────────

def _rate(memory, aid, **update):
    before = memory.message_feedback.list(aid, "s")
    current = next((i for i in before["items"] if not i["quote"]), None)
    data = {"revision": current["revision"] if current else 0, **update}
    if current:
        data["feedback_id"] = current["feedback_id"]
    after = memory.message_feedback.save(aid, "s", data)
    apply_message_feedback(memory, message_id=aid, session_id="s", before=before, after=after)


def test_low_rating_scores_skills_and_creates_case(memory):
    ctl = controller(memory)
    _, _, aid = persist(ctl, memory, sid="s", user_text="比较两款相机", answer="A 更好", skills=["camera-review"])
    _rate(memory, aid, rating=2, reasons=["偏离问题"], reason="没比较画质")
    outcome = memory.conn.execute("SELECT quality_score, success FROM skill_outcomes").fetchone()
    assert outcome["success"] == 0 and outcome["quality_score"] == 0.25
    [case] = memory.eval_cases.list(kind="negative_feedback")
    assert case["prompt"] == "比较两款相机" and "没比较画质" in case["expectation"]
    assert memory.conn.execute("SELECT count(*) FROM reflections WHERE success=0").fetchone()[0] == 1

    # Re-saving the same rating changes nothing; raising it flips the case.
    _rate(memory, aid, rating=2, reasons=["偏离问题"], reason="没比较画质")
    assert memory.conn.execute("SELECT count(*) FROM skill_outcomes").fetchone()[0] == 1
    _rate(memory, aid, rating=5, reasons=["切中问题"], reason="")
    assert memory.eval_cases.list(kind="negative_feedback") == []
    assert len(memory.eval_cases.list(kind="endorsed")) == 1


def test_feedback_case_falls_back_to_preceding_user_message(memory):
    uid = user_msg(memory, "旧会话里的问题")
    aid = assistant_msg(memory, "旧答案")
    _rate(memory, aid, rating=1)
    [case] = memory.eval_cases.list(kind="negative_feedback")
    assert case["prompt"] == "旧会话里的问题" and case["prompt_message_id"] == uid
    memory.delete_message_derived_data(aid)
    assert memory.eval_cases.list(status="") == []


# ── Runner ───────────────────────────────────────────────────────────────────

def test_runner_records_results(memory):
    memory.eval_cases.upsert(kind="correction", session_id="s", source_message_id="event:m",
                             prompt="q", response="bad", expectation="be good")
    cases = memory.eval_cases.list()

    async def answer(case):
        return "good"

    async def judge(case, response):
        return response == "good", "matches"

    report = asyncio.run(run_eval_cases(memory, cases, answer_fn=answer, judge_fn=judge))
    assert (report.passed, report.total) == (1, 1)
    [run] = memory.eval_cases.last_runs(cases[0]["case_id"])
    assert run["passed"] == 1 and run["batch_id"] == report.batch_id
