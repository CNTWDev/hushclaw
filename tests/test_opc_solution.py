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
        self.created_agents = []

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
        ] + self.created_agents

    def create_agent(
        self,
        name,
        description="",
        system_prompt="",
        instructions="",
        role="specialist",
        team="",
        reports_to="",
        capabilities=None,
        tools=None,
    ):
        if any(agent.get("name") == name for agent in self.list_agents()):
            raise ValueError(f"Agent '{name}' already exists.")
        self.created_agents.append({
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "instructions": instructions,
            "role": role,
            "team": team,
            "reports_to": reports_to,
            "capabilities": list(capabilities or []),
            "tools": list(tools or []),
        })

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


def test_opc_employee_onboarding_drafts_skill_recommendations_before_agent_creation():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]

        draft = asyncio.run(opc.draft_employee(
            "Create a digital employee for competitor research and market trend briefs."
        ))

        assert draft["status"] == "draft"
        assert draft["display_name"] == "Market Researcher"
        assert draft["agent_name"] == "market-researcher"
        assert "research" in draft["capabilities"]
        assert gateway.created_agents == []
        recommendations = draft["skill_recommendations"]
        assert recommendations
        assert {item["status"] for item in recommendations} == {"suggested"}
        assert {item["kind"] for item in recommendations} == {"create"}


def test_opc_employee_skill_approval_only_marks_recommendation():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]

        draft = asyncio.run(opc.draft_employee("Need a writer for content drafts and review."))
        rec = draft["skill_recommendations"][0]
        approved = opc.approve_employee_skill(draft["id"], rec["id"])

        assert approved["status"] == "approved"
        assert gateway.created_agents == []
        assert len(opc.list_skill_recommendations()) == len(draft["skill_recommendations"])


def test_opc_create_employee_from_draft_registers_agent_and_team_member():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]
        opc.sync_employees_from_agents()
        team = opc.create_team({
            "name": "Research Team",
            "purpose": "Research market options",
            "member_agents": ["ceo"],
            "facilitator": "ceo",
        })
        draft = asyncio.run(opc.draft_employee(
            "Create a digital employee for competitor research.",
            team_id=team["id"],
        ))

        result = opc.create_employee_from_draft(draft["id"])

        assert gateway.created_agents[0]["name"] == "market-researcher"
        assert gateway.created_agents[0]["reports_to"] == "ceo"
        assert result["draft"]["status"] == "created"
        assert result["draft"]["created_agent_name"] == "market-researcher"
        assert result["employee"]["agent_name"] == "market-researcher"
        updated_team = opc.store.get("team", team["id"])
        assert updated_team is not None
        assert updated_team["member_agents"] == ["ceo", "market-researcher"]


def test_opc_create_employee_from_draft_uses_unique_agent_name():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        gateway.create_agent("market-researcher")
        opc = AgentOSService(gateway).solutions["opc"]

        draft = asyncio.run(opc.draft_employee("Need market research support."))
        result = opc.create_employee_from_draft(draft["id"])

        assert result["draft"]["created_agent_name"] == "market-researcher-2"
        assert gateway.created_agents[-1]["name"] == "market-researcher-2"


def test_opc_records_can_be_updated_and_archived_without_breaking_history():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]
        opc.sync_employees_from_agents()
        team = opc.create_team({
            "name": "Ops",
            "purpose": "Original",
            "member_agents": ["ceo", "operator"],
            "facilitator": "ceo",
        })
        goal = opc.create_goal({
            "objective": "Ship the offer",
            "team_id": team["id"],
            "priority": 1,
        })

        updated_team = opc.update_team(team["id"], {
            "name": "Core Ops",
            "purpose": "Updated",
            "member_agents": ["ceo"],
            "facilitator": "ceo",
        })
        updated_goal = opc.update_goal(goal["id"], {
            "objective": "Ship the first offer",
            "team_id": team["id"],
            "priority": 3,
        })
        done_goal = opc.complete_goal(goal["id"])
        employee = opc.update_employee("emp-operator", {
            "display_name": "Operator Lead",
            "capabilities": "ops, delivery",
        })

        assert updated_team["name"] == "Core Ops"
        assert updated_goal["objective"] == "Ship the first offer"
        assert done_goal["status"] == "done"
        assert employee["display_name"] == "Operator Lead"
        assert employee["capabilities"] == ["ops", "delivery"]

        opc.archive_goal(goal["id"])
        opc.archive_team(team["id"])
        assert goal["id"] not in {item["id"] for item in opc.list_goals()}
        assert team["id"] not in {item["id"] for item in opc.list_teams()}
        assert opc.store.get("channel", f"chan-{team['id']}")["status"] == "archived"


def test_opc_archiving_employee_hides_it_and_removes_team_membership():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]
        opc.sync_employees_from_agents()
        team = opc.create_team({
            "name": "Core",
            "member_agents": ["ceo", "operator"],
            "facilitator": "operator",
        })

        archived = opc.archive_employee("emp-operator")
        updated_team = opc.store.get("team", team["id"])

        assert archived["status"] == "archived"
        assert "operator" not in {item["agent_name"] for item in opc.list_employees()}
        assert updated_team["member_agents"] == ["ceo"]
        assert updated_team["facilitator"] == "ceo"


def test_opc_can_delete_uncreated_employee_draft():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), embed_provider="local")
        gateway = _FakeGateway(mem)
        opc = AgentOSService(gateway).solutions["opc"]
        draft = asyncio.run(opc.draft_employee("Need market research support."))

        deleted = opc.delete_employee_draft(draft["id"])

        assert deleted["status"] == "deleted"
        assert draft["id"] not in {item["id"] for item in opc.list_employee_drafts()}
