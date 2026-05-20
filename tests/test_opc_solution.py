from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from hushclaw.memory import MemoryStore
from hushclaw.os_api import AgentOSService


class _FakeGateway:
    def __init__(self, memory):
        self.memory = memory
        self.base_agent = SimpleNamespace(registry=SimpleNamespace(list_tools=lambda: []))
        self.broadcast_calls = []
        self.execute_calls = []

    def list_agents(self):
        return [
            {
                "name": "ceo",
                "description": "Facilitates OPC decisions",
                "role": "commander",
                "team": "core",
                "reports_to": "",
                "capabilities": ["strategy"],
            },
            {
                "name": "operator",
                "description": "Turns plans into execution",
                "role": "specialist",
                "team": "core",
                "reports_to": "ceo",
                "capabilities": ["operations"],
            },
        ]

    async def broadcast(self, names, task):
        self.broadcast_calls.append((list(names), task))
        return {name: f"{name} response for {task[:24]}" for name in names}

    async def execute(self, name, task):
        self.execute_calls.append((name, task))
        return f"summary by {name}: decisions, risks, next work item"


def test_opc_solution_syncs_agents_and_registers_on_agent_os():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        os_api = AgentOSService(_FakeGateway(mem))

        opc = os_api.solutions["opc"]
        employees = opc.sync_employees_from_agents()

        assert os_api.runtime.profile()["solutions"] == {"opc": True}
        assert [item["agent_name"] for item in employees] == ["ceo", "operator"]
        assert employees[0]["display_name"] == "ceo"


def test_opc_team_goal_discussion_and_approval_flow():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        os_api = AgentOSService(gateway)
        opc = os_api.solutions["opc"]
        opc.sync_employees_from_agents()

        team = opc.create_team({
            "name": "Core Team",
            "purpose": "Operate the one-person company",
            "member_agents": ["ceo", "operator"],
            "facilitator": "ceo",
        })
        channel = opc.list_channels()[0]
        assert channel["team_id"] == team["id"]
        goal = opc.create_goal({
            "objective": "Launch a lightweight offer",
            "success_criteria": "A clear offer and first outreach plan",
            "team_id": team["id"],
            "priority": 2,
        })

        plan = asyncio.run(opc.plan_goal(goal["id"]))
        approved = opc.approve_goal_plan(goal["id"])

        assert gateway.broadcast_calls
        assert gateway.execute_calls[0][0] == "ceo"
        assert plan["goal"]["status"] == "planned"
        assert plan["discussion"]["participants"] == ["ceo", "operator"]
        assert plan["work_items"][0]["status"] == "draft"
        assert approved["goal"]["status"] == "active"
        assert approved["todos"][0]["title"].startswith("Advance OPC goal")
        assert opc.overview()["work_items"][0]["todo_id"] == approved["todos"][0]["todo_id"]
        history = opc.get_channel_history(channel["id"])
        assert any(item["sender_type"] == "user" for item in history)
        assert any(item["sender_type"] == "agent" for item in history)
        events = mem._event_store.type_prefix_events("audit:opc.", limit=10)
        assert {event["type"] for event in events} == {
            "audit:opc.channel.message",
            "audit:opc.goal.approved",
            "audit:opc.goal.planned",
            "audit:opc.discussion.completed",
        }


def test_opc_rejects_unknown_team_agents():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        opc = AgentOSService(_FakeGateway(mem)).solutions["opc"]

        try:
            opc.create_team({"name": "Bad Team", "member_agents": ["missing"]})
        except ValueError as exc:
            assert "unknown agent(s): missing" in str(exc)
        else:
            raise AssertionError("Expected unknown team member to fail")


def test_opc_channel_messages_call_digital_employees_by_mention_rules():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]
        opc.sync_employees_from_agents()
        team = opc.create_team({
            "name": "Design Team",
            "member_agents": ["ceo", "operator"],
            "facilitator": "ceo",
        })
        channel = opc.ensure_channel_for_team(team)

        note = asyncio.run(opc.send_channel_message(channel["id"], "Please keep this in mind."))
        assert gateway.broadcast_calls == []
        assert [item["sender_type"] for item in note["messages"]] == ["user"]

        all_result = asyncio.run(opc.send_channel_message(channel["id"], "@all review this"))
        assert gateway.broadcast_calls[-1][0] == ["ceo", "operator"]
        assert [item["agent_name"] for item in all_result["replies"]] == ["ceo", "operator"]

        one_result = asyncio.run(opc.send_channel_message(channel["id"], "@operator estimate effort"))
        assert gateway.broadcast_calls[-1][0] == ["operator"]
        assert [item["agent_name"] for item in one_result["replies"]] == ["operator"]

        history = opc.get_channel_history(channel["id"])
        assert [item["sender_type"] for item in history] == [
            "user",
            "user",
            "agent",
            "agent",
            "user",
            "agent",
        ]
