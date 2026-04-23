"""Tests for the scheduler: cron matching and MemoryStore CRUD."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hushclaw.scheduler import _cron_matches


# ---------------------------------------------------------------------------
# Cron matching
# ---------------------------------------------------------------------------

class TestCronMatches:
    def test_every_day_at_8(self):
        assert _cron_matches("0 8 * * *", datetime(2026, 3, 13, 8, 0))

    def test_every_day_at_8_wrong_hour(self):
        assert not _cron_matches("0 8 * * *", datetime(2026, 3, 13, 9, 0))

    def test_every_day_at_8_wrong_minute(self):
        assert not _cron_matches("0 8 * * *", datetime(2026, 3, 13, 8, 1))

    def test_every_30_minutes(self):
        assert _cron_matches("*/30 * * * *", datetime(2026, 3, 13, 10, 0))
        assert _cron_matches("*/30 * * * *", datetime(2026, 3, 13, 10, 30))
        assert not _cron_matches("*/30 * * * *", datetime(2026, 3, 13, 10, 15))

    def test_specific_weekday(self):
        # 2026-03-16 is Monday (weekday()=0)
        assert _cron_matches("0 9 * * 0", datetime(2026, 3, 16, 9, 0))
        # 2026-03-17 is Tuesday (weekday()=1)
        assert not _cron_matches("0 9 * * 0", datetime(2026, 3, 17, 9, 0))

    def test_star_matches_all(self):
        assert _cron_matches("* * * * *", datetime(2026, 3, 13, 14, 37))

    def test_comma_list(self):
        assert _cron_matches("0 8,12,18 * * *", datetime(2026, 3, 13, 12, 0))
        assert not _cron_matches("0 8,12,18 * * *", datetime(2026, 3, 13, 10, 0))

    def test_range(self):
        assert _cron_matches("0 9-17 * * *", datetime(2026, 3, 13, 12, 0))
        assert not _cron_matches("0 9-17 * * *", datetime(2026, 3, 13, 18, 0))

    def test_invalid_expr(self):
        assert not _cron_matches("not a cron", datetime(2026, 3, 13, 8, 0))
        assert not _cron_matches("0 8 * *", datetime(2026, 3, 13, 8, 0))  # 4 fields


# ---------------------------------------------------------------------------
# MemoryStore CRUD
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_store(tmp_path: Path):
    from hushclaw.memory.store import MemoryStore
    return MemoryStore(data_dir=tmp_path)


class TestScheduledTaskCRUD:
    def test_add_and_list(self, memory_store):
        task_id = memory_store.add_scheduled_task("0 8 * * *", "Say hello", "default")
        assert len(task_id) == 36  # UUID format
        tasks = memory_store.list_scheduled_tasks()
        assert len(tasks) == 1
        assert tasks[0]["cron"] == "0 8 * * *"
        assert tasks[0]["prompt"] == "Say hello"
        assert tasks[0]["agent"] == "default"

    def test_cancel(self, memory_store):
        task_id = memory_store.add_scheduled_task("0 8 * * *", "Test prompt")
        ok = memory_store.cancel_scheduled_task(task_id)
        assert ok
        # list_scheduled_tasks returns all tasks (including disabled); active list is empty
        assert memory_store.list_active_scheduled_tasks() == []
        all_tasks = memory_store.list_scheduled_tasks()
        assert len(all_tasks) == 1
        assert all_tasks[0]["enabled"] == 0

    def test_cancel_nonexistent(self, memory_store):
        assert not memory_store.cancel_scheduled_task("nonexistent-id")

    def test_get_due(self, memory_store):
        memory_store.add_scheduled_task("0 8 * * *", "Morning task")
        memory_store.add_scheduled_task("0 20 * * *", "Evening task")
        # 08:00 — morning task is due
        due = memory_store.get_due_scheduled_tasks(datetime(2026, 3, 13, 8, 0))
        assert len(due) == 1
        assert due[0]["prompt"] == "Morning task"
        # 20:00 — evening task is due
        due2 = memory_store.get_due_scheduled_tasks(datetime(2026, 3, 13, 20, 0))
        assert len(due2) == 1
        assert due2[0]["prompt"] == "Evening task"

    def test_update_last_run(self, memory_store):
        task_id = memory_store.add_scheduled_task("* * * * *", "Frequent task")
        ts = datetime(2026, 3, 13, 8, 0)
        memory_store.update_scheduled_task_last_run(task_id, ts)
        tasks = memory_store.list_scheduled_tasks()
        assert tasks[0]["last_run"] == ts.isoformat()


@pytest.mark.asyncio
async def test_scheduler_session_mode_job():
    from hushclaw.scheduler import Scheduler
    memory = SimpleNamespace()
    calls = []

    class _Gateway:
        def __init__(self):
            self._base_agent = SimpleNamespace(config=SimpleNamespace(gateway=SimpleNamespace(scheduled_session_mode="job")))

        async def execute(self, agent, prompt, session_id=None):
            calls.append((agent, prompt, session_id))

    s = Scheduler(memory, _Gateway())
    job = {"id": "12345678-aaaa-bbbb-cccc-ddddeeeeffff", "agent": "default", "prompt": "run"}
    await s._run_job(job)
    assert calls
    assert calls[0][2] == "sched_12345678"


@pytest.mark.asyncio
async def test_scheduler_session_mode_run():
    from hushclaw.scheduler import Scheduler
    memory = SimpleNamespace()
    calls = []

    class _Gateway:
        def __init__(self):
            self._base_agent = SimpleNamespace(config=SimpleNamespace(gateway=SimpleNamespace(scheduled_session_mode="run")))

        async def execute(self, agent, prompt, session_id=None):
            calls.append((agent, prompt, session_id))

    s = Scheduler(memory, _Gateway())
    job = {"id": "12345678-aaaa-bbbb-cccc-ddddeeeeffff", "agent": "default", "prompt": "run"}
    await s._run_job(job)
    assert calls
    assert calls[0][2].startswith("sched_12345678_")
