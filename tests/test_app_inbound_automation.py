from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hushclaw.app_connectors.inbound import InboundAutomationWorker
from hushclaw.app_connectors.x_stream import XFilteredStreamWorker
from hushclaw.config import loader as loader_mod
from hushclaw.config.loader import load_config
from hushclaw.config.schema import InboundAutomationConfig, InboundAutomationRuleConfig
from hushclaw.tools.base import ToolResult


class _FakeInboxStore:
    def __init__(self, events: list[dict]) -> None:
        self._events = {str(item["event_id"]): dict(item) for item in events}

    def list_app_inbox_events(self, connector_id: str = "", status: str = "", *, limit: int = 50, offset: int = 0) -> list[dict]:
        items = list(self._events.values())
        if connector_id:
            items = [item for item in items if item.get("connector_id") == connector_id]
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: int(item.get("updated") or 0), reverse=True)
        return [self._clone(item) for item in items[offset: offset + limit]]

    def patch_app_inbox_event(self, event_id: str, *, status: str | None = None, payload_patch: dict | None = None, **_kwargs):
        event = self._events.get(event_id)
        if event is None:
            return None
        if status is not None:
            event["status"] = status
        payload = dict(event.get("payload") or {})
        if isinstance(payload_patch, dict):
            payload.update(payload_patch)
        event["payload"] = payload
        event["updated"] = int(event.get("updated") or 0) + 1
        return self._clone(event)

    def claim_app_inbox_event(self, event_id: str, *, from_statuses, to_status: str = "pending", payload_patch: dict | None = None):
        event = self._events.get(event_id)
        if event is None or event.get("status") not in set(from_statuses):
            return None
        event["status"] = to_status
        payload = dict(event.get("payload") or {})
        if isinstance(payload_patch, dict):
            payload.update(payload_patch)
        event["payload"] = payload
        event["updated"] = int(event.get("updated") or 0) + 1
        return self._clone(event)

    @staticmethod
    def _clone(item: dict) -> dict:
        out = dict(item)
        out["payload"] = dict(item.get("payload") or {})
        return out


def test_load_config_parses_inbound_automation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(loader_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "_data_dir", lambda: tmp_path)
    (tmp_path / ".hushclaw.toml").write_text(
        """
[app_connectors.inbound_automation]
enabled = true
poll_interval_seconds = 7
batch_size = 4
default_agent = "ops"
default_action = "queue_only"
max_reply_chars = 220

[[app_connectors.inbound_automation.rules]]
name = "x-brand-replies"
enabled = true
connector_id = "x"
event_types = ["reply_to_me"]
rule_tags = ["brand"]
action = "auto_reply"
agent = "social"
cooldown_seconds = 120
cooldown_scope = "thread_author"
require_allow_actions = true
        """,
        encoding="utf-8",
    )

    config = load_config(project_dir=tmp_path)

    assert config.app_connectors.inbound_automation.enabled is True
    assert config.app_connectors.inbound_automation.default_agent == "ops"
    assert config.app_connectors.inbound_automation.max_reply_chars == 220
    rule = config.app_connectors.inbound_automation.rules[0]
    assert rule.connector_id == "x"
    assert rule.event_types == ["reply_to_me"]
    assert rule.rule_tags == ["brand"]
    assert rule.agent == "social"


def test_x_stream_stores_normalized_inbound_message():
    captured: dict = {}

    class _MemoryStore:
        def upsert_app_inbox_event(self, **kwargs):
            captured.update(kwargs)
            return kwargs

    worker = XFilteredStreamWorker(SimpleNamespace(), SimpleNamespace(), _MemoryStore())
    worker._account_profile = {"id": "me-1", "username": "myacct"}

    worker._store_stream_payload({
        "data": {
            "id": "tweet-2",
            "text": "@myacct can you take a look?",
            "author_id": "user-9",
            "conversation_id": "conv-1",
            "referenced_tweets": [{"type": "reply", "id": "tweet-1"}],
        },
        "includes": {
            "users": [{"id": "user-9", "username": "alice"}],
            "tweets": [{"id": "tweet-1", "author_id": "me-1"}],
        },
        "matching_rules": [{"tag": "hushclaw:brand"}],
    })

    assert captured["event_type"] == "inbound.message"
    assert captured["external_id"] == "tweet-2"
    assert captured["status"] == "unread"
    assert captured["payload"]["normalized_event_type"] == "reply_to_me"
    assert captured["payload"]["matched_rule_tags"] == ["brand"]
    assert captured["payload"]["thread_id"] == "conv-1"
    assert captured["payload"]["target_external_id"] == "tweet-1"


@pytest.mark.asyncio
async def test_inbound_worker_auto_replies_when_rule_matches(monkeypatch):
    event = {
        "event_id": "evt-1",
        "connector_id": "x",
        "event_type": "inbound.message",
        "external_id": "tweet-2",
        "title": "Need help",
        "body": "Can you help with this?",
        "source_url": "https://x.com/i/web/status/tweet-2",
        "status": "unread",
        "created": 1,
        "updated": 1,
        "payload": {
            "normalized_event_type": "reply_to_me",
            "thread_id": "conv-1",
            "author_external_id": "user-9",
            "author_username": "alice",
            "target_external_id": "tweet-1",
            "matched_rule_tags": ["brand"],
        },
    }
    store = _FakeInboxStore([event])
    sent: dict = {}

    def _fake_reply(config, secrets, post_id: str, text: str, memory_store=None):
        sent["allow_actions"] = config.allow_actions
        sent["post_id"] = post_id
        sent["text"] = text
        sent["memory_store"] = memory_store
        return ToolResult.ok(json.dumps({"provider": "x", "action": "reply", "id": "reply-1"}))

    monkeypatch.setattr("hushclaw.app_connectors.inbound.x_connector.reply", _fake_reply)

    class _Gateway:
        async def execute(self, agent_name: str, prompt: str, session_id: str | None = None):
            assert agent_name == "social"
            assert "Can you help with this?" in prompt
            assert session_id == "app-inbound:x:conv-1"
            return "Absolutely, send me the details."

    cfg = SimpleNamespace(
        x=SimpleNamespace(allow_actions=True),
        inbound_automation=InboundAutomationConfig(
            enabled=True,
            batch_size=5,
            default_agent="default",
            default_action="queue_only",
            max_reply_chars=120,
            rules=[
                InboundAutomationRuleConfig(
                    name="reply-brand",
                    connector_id="x",
                    event_types=["reply_to_me"],
                    rule_tags=["brand"],
                    action="auto_reply",
                    agent="social",
                    cooldown_seconds=0,
                )
            ],
        ),
    )
    worker = InboundAutomationWorker(cfg, _Gateway(), store, secrets=SimpleNamespace())

    processed = await worker._process_batch()

    assert processed == 1
    updated = store.list_app_inbox_events(limit=10)[0]
    assert updated["status"] == "auto_replied"
    assert updated["payload"]["policy_decision"] == "auto_reply"
    assert updated["payload"]["policy_rule"] == "reply-brand"
    assert updated["payload"]["reply_text"] == "Absolutely, send me the details."
    assert sent["post_id"] == "tweet-2"
    assert sent["memory_store"] is store


@pytest.mark.asyncio
async def test_inbound_worker_queues_unmatched_events():
    store = _FakeInboxStore([
        {
            "event_id": "evt-2",
            "connector_id": "x",
            "event_type": "inbound.message",
            "external_id": "tweet-3",
            "title": "Question",
            "body": "What do you think?",
            "source_url": "",
            "status": "unread",
            "created": 1,
            "updated": 1,
            "payload": {
                "normalized_event_type": "keyword_match",
                "thread_id": "conv-2",
                "author_external_id": "user-2",
                "matched_rule_tags": ["random"],
            },
        }
    ])

    class _Gateway:
        async def execute(self, agent_name: str, prompt: str, session_id: str | None = None):
            raise AssertionError("execute should not be called for unmatched events")

    cfg = SimpleNamespace(
        x=SimpleNamespace(allow_actions=True),
        inbound_automation=InboundAutomationConfig(
            enabled=True,
            batch_size=5,
            default_agent="default",
            default_action="queue_only",
            rules=[
                InboundAutomationRuleConfig(
                    name="reply-brand",
                    connector_id="x",
                    event_types=["reply_to_me"],
                    rule_tags=["brand"],
                    action="auto_reply",
                )
            ],
        ),
    )
    worker = InboundAutomationWorker(cfg, _Gateway(), store, secrets=SimpleNamespace())

    processed = await worker._process_batch()

    assert processed == 1
    updated = store.list_app_inbox_events(limit=10)[0]
    assert updated["status"] == "classified"
    assert updated["payload"]["policy_decision"] == "queue_only"
    assert updated["payload"]["policy_reason"] == "default_action"
