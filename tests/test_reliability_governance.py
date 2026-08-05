from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from hushclaw.memory.outbox import DeliveryOutboxStore
from hushclaw.memory.store import MemoryStore
from hushclaw.os_api import AgentOSService
from hushclaw.os_contracts import AgentOSOutboundMessage, ConversationAddress
from hushclaw.scheduler import Scheduler


def test_task_claim_is_atomic_and_dependency_gated(tmp_path: Path):
    memory = MemoryStore(tmp_path)
    dependency = memory.create_task("First")
    dependent = memory.create_task("Second", dependencies=[dependency["task_id"]])

    assert memory.claim_task(dependent["task_id"], worker_id="early") is None

    dependency_run = memory.claim_task(dependency["task_id"], worker_id="owner")
    assert dependency_run
    assert memory.complete_task_run(dependency_run["run_id"], "done")

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(
            lambda worker: memory.claim_task(dependent["task_id"], worker_id=worker),
            ("worker-a", "worker-b"),
        ))
    winners = [item for item in claims if item]
    assert len(winners) == 1
    assert winners[0]["attempt"] == 1
    assert winners[0]["lease_token"].startswith("lease-")
    memory.close()


def test_stale_run_is_fenced_and_heartbeat_renews_lease(tmp_path: Path):
    memory = MemoryStore(tmp_path)
    task = memory.create_task("Long work")
    first = memory.claim_task(task["task_id"], worker_id="worker-a", ttl_seconds=1)
    assert first
    assert memory.heartbeat_task_run(first["run_id"], first["lease_token"], ttl_seconds=30)
    renewed = memory.get_task_run(first["run_id"])
    assert renewed["heartbeat_at"] > 0
    assert renewed["claim_expires_at"] > first["claim_expires_at"]

    assert memory.mark_stale_task_runs(now=renewed["claim_expires_at"] + 1) == 1
    assert memory.retry_task(task["task_id"])
    second = memory.claim_task(task["task_id"], worker_id="worker-b")
    assert second and second["attempt"] == 2

    assert not memory.complete_task_run(
        first["run_id"], "late result", lease_token=first["lease_token"]
    )
    assert memory.complete_task_run(
        second["run_id"], "current result", lease_token=second["lease_token"]
    )
    assert memory.get_task(task["task_id"])["status"] == "done"
    memory.close()


def test_completion_contract_rejects_claims_without_required_evidence(tmp_path: Path):
    memory = MemoryStore(tmp_path)
    task = memory.create_task(
        "Build report",
        acceptance_criteria=["A report file exists"],
        proof_required="artifact",
    )
    first = memory.claim_task(task["task_id"], worker_id="worker-a")
    assert first
    assert not memory.complete_task_run(
        first["run_id"], "I finished it", lease_token=first["lease_token"]
    )
    rejected = memory.get_task_run(first["run_id"])
    assert rejected["completion_state"] == "rejected"
    assert "artifact" in rejected["completion_note"].lower()
    assert memory.get_task(task["task_id"])["status"] == "blocked"

    assert memory.retry_task(task["task_id"])
    second = memory.claim_task(task["task_id"], worker_id="worker-b")
    assert second
    assert memory.complete_task_run(
        second["run_id"],
        "Report attached",
        evidence=[{"kind": "artifact", "artifact_id": "file-1", "url": "/files/file-1"}],
        lease_token=second["lease_token"],
    )
    verified = memory.get_task_run(second["run_id"])
    assert verified["completion_state"] == "verified"
    assert verified["evidence"][0]["artifact_id"] == "file-1"
    memory.close()


@pytest.mark.asyncio
async def test_scheduler_collects_evidence_and_honors_workspace_and_model(tmp_path: Path):
    memory = MemoryStore(tmp_path)
    seen = {}

    async def event_stream(*args, **kwargs):
        seen["kwargs"] = kwargs
        yield {
            "type": "tool_result",
            "tool": "write_file",
            "result": "ok",
            "is_error": False,
            "artifacts": [{"artifact_id": "report-1", "url": "/files/report-1", "title": "Report"}],
        }
        yield {"type": "done", "text": "Created the report"}

    gateway = SimpleNamespace(memory=memory, event_stream=event_stream)
    gateway._os_api = AgentOSService(gateway)
    scheduler = Scheduler(memory, gateway)
    task = memory.create_task(
        "Create report",
        workspace="research",
        model_override="model-for-task",
        proof_required="artifact",
    )

    result = await scheduler.run_work_task_now(task["task_id"])

    assert result["ok"] is True
    assert seen["kwargs"]["workspace"] == "research"
    assert seen["kwargs"]["model_override"] == "model-for-task"
    run = memory.list_task_runs(task_id=task["task_id"])[0]
    assert run["completion_state"] == "verified"
    assert {item["kind"] for item in run["evidence"]} == {"tool", "artifact", "response"}
    memory.close()


def test_outbox_retries_recovers_and_dead_letters(tmp_path: Path):
    memory = MemoryStore(tmp_path)
    outbox = DeliveryOutboxStore(memory.conn)
    message = AgentOSOutboundMessage(
        address=ConversationAddress(provider="telegram", conversation_id="chat-1"),
        body="hello",
        idempotency_key="reply-1",
    )
    receipt = outbox.enqueue(message)
    assert outbox.claim(receipt.delivery_id)
    retried = outbox.mark_failed(receipt.delivery_id, "timeout", max_attempts=2, now=100)
    assert retried.status == "retry"

    due = outbox.claim_due(["telegram"], now=106)
    assert len(due) == 1
    assert due[0][1].body == "hello"
    dead = outbox.mark_failed(receipt.delivery_id, "timeout again", max_attempts=2, now=106)
    assert dead.status == "dead_letter"
    assert outbox.summary()["dead_letter"] == 1

    second = outbox.enqueue(AgentOSOutboundMessage(
        address=ConversationAddress(provider="telegram", conversation_id="chat-2"),
        body="recover me",
    ))
    assert outbox.claim(second.delivery_id)
    assert outbox.recover_in_flight() == 1
    assert outbox.get(second.delivery_id).status == "retry"
    memory.close()


def test_ui_consolidates_insights_and_exposes_run_governance():
    root = Path(__file__).resolve().parents[1]
    index = (root / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    tasks = (root / "hushclaw" / "web" / "modules" / "tasks.js").read_text(encoding="utf-8")

    assert 'data-tab="insights"' not in index
    assert 'data-sub="insights"' in index
    assert 'id="panel-insights"' not in index
    assert 'id="work-task-health"' in index
    assert 'id="work-task-proof-input"' in index
    assert "completion_state" in tasks
    assert "run.lease_token" in tasks


def test_streaming_connectors_share_one_outbox_sender_contract():
    from hushclaw.connectors.dingtalk import DingTalkConnector
    from hushclaw.connectors.discord import DiscordConnector
    from hushclaw.connectors.slack import SlackConnector
    from hushclaw.connectors.wecom import WeChatWorkConnector

    for connector in (DingTalkConnector, DiscordConnector, SlackConnector, WeChatWorkConnector):
        assert not inspect.isabstract(connector)
