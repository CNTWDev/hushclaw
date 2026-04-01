from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_skill_module():
    root = Path(__file__).resolve().parents[1]
    mod_path = (
        root
        / "skill-packages"
        / "hushclaw-skill-x-operator"
        / "tools"
        / "x_operator_tools.py"
    )
    spec = importlib.util.spec_from_file_location("x_operator_tools", mod_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_trigger_phrase_contract():
    mod = _load_skill_module()
    assert mod._task_prompt_matches("执行 X 运营一轮 tick")
    assert mod._task_prompt_matches("请执行 X 运营一轮 tick，处理 mention")
    assert not mod._task_prompt_matches("执行一轮社媒任务")


def test_http_error_classification():
    mod = _load_skill_module()
    assert mod._classify_http_status(401)["fatal"]
    assert mod._classify_http_status(403)["fatal"]
    assert mod._classify_http_status(429)["retryable"]
    assert mod._classify_http_status(503)["retryable"]
    ok = mod._classify_http_status(200)
    assert not ok["retryable"]
    assert not ok["fatal"]


def test_action_key_is_stable():
    mod = _load_skill_module()
    assert mod._make_action_key("reply", "123") == "reply:123"
    assert mod._make_action_key("reply", "123 ") == "reply:123"


def test_lock_acquire_and_release():
    mod = _load_skill_module()
    state = mod._default_state()
    ok, run_id = mod._acquire_run_lock(state)
    assert ok
    assert run_id

    ok2, _ = mod._acquire_run_lock(state)
    assert not ok2

    mod._release_run_lock(state, run_id)
    ok3, _ = mod._acquire_run_lock(state)
    assert ok3
