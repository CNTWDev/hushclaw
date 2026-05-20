"""OPC service: digital employees, teams, goals, and discussions."""
from __future__ import annotations

import asyncio
from typing import Any

from hushclaw.solutions.opc.store import OpcStore
from hushclaw.util.ids import make_id


class OpcService:
    """Product service built above AgentOS and the gateway agent runtime."""

    def __init__(self, os_api) -> None:
        self.os = os_api
        self.gateway = os_api.gateway
        self.store = OpcStore(os_api.gateway.memory.conn)

    def overview(self) -> dict:
        self.sync_employees_from_agents()
        return {
            "employees": self.list_employees(),
            "teams": self.list_teams(),
            "goals": self.list_goals(),
            "discussions": self.list_discussions(limit=20),
            "work_items": self.list_work_items(),
        }

    def sync_employees_from_agents(self) -> list[dict]:
        synced: list[dict] = []
        existing_by_agent = {
            item.get("agent_name"): item
            for item in self.store.list("employee", limit=1000)
        }
        for agent in self.os.agents.list():
            agent_name = str(agent.get("name") or "").strip()
            if not agent_name:
                continue
            current = existing_by_agent.get(agent_name) or {}
            employee_id = current.get("id") or f"emp-{agent_name}"
            item = self.store.upsert("employee", employee_id, {
                "agent_name": agent_name,
                "display_name": current.get("display_name") or agent_name,
                "role": agent.get("role") or current.get("role") or "specialist",
                "team": agent.get("team") or current.get("team") or "",
                "reports_to": agent.get("reports_to") or current.get("reports_to") or "",
                "responsibilities": current.get("responsibilities") or [],
                "capabilities": agent.get("capabilities") or current.get("capabilities") or [],
                "description": agent.get("description") or current.get("description") or "",
                "status": current.get("status") or "active",
            })
            synced.append(item)
        return synced

    def list_employees(self) -> list[dict]:
        return self.store.list("employee")

    def create_team(self, data: dict[str, Any]) -> dict:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("team name is required")
        member_agents = self._normalize_agent_names(data.get("member_agents") or data.get("members") or [])
        self._validate_agents_exist(member_agents)
        facilitator = str(data.get("facilitator") or data.get("facilitator_agent") or "").strip()
        if facilitator:
            self._validate_agents_exist([facilitator])
        elif member_agents:
            facilitator = member_agents[0]
        team_id = str(data.get("id") or "").strip() or make_id("opc-team-")
        return self.store.upsert("team", team_id, {
            "name": name,
            "purpose": str(data.get("purpose") or ""),
            "member_agents": member_agents,
            "facilitator": facilitator,
            "status": str(data.get("status") or "active"),
        })

    def update_team(self, team_id: str, fields: dict[str, Any]) -> dict:
        current = self.store.get("team", team_id)
        if current is None:
            raise ValueError(f"unknown team: {team_id}")
        data = {**current, **fields, "id": team_id}
        return self.create_team(data)

    def list_teams(self) -> list[dict]:
        return self.store.list("team")

    def create_goal(self, data: dict[str, Any]) -> dict:
        objective = str(data.get("objective") or data.get("title") or "").strip()
        if not objective:
            raise ValueError("goal objective is required")
        team_id = str(data.get("team_id") or "").strip()
        if team_id and self.store.get("team", team_id) is None:
            raise ValueError(f"unknown team: {team_id}")
        goal_id = str(data.get("id") or "").strip() or make_id("opc-goal-")
        return self.store.upsert("goal", goal_id, {
            "objective": objective,
            "success_criteria": str(data.get("success_criteria") or ""),
            "priority": int(data.get("priority") or 0),
            "due_at": str(data.get("due_at") or ""),
            "team_id": team_id,
            "status": str(data.get("status") or "draft"),
            "plan_discussion_id": str(data.get("plan_discussion_id") or ""),
        })

    def list_goals(self) -> list[dict]:
        return self.store.list("goal")

    def list_discussions(self, *, limit: int = 200) -> list[dict]:
        return self.store.list("discussion", limit=limit)

    def list_work_items(self, *, limit: int = 200) -> list[dict]:
        return self.store.list("work_item", limit=limit)

    async def plan_goal(self, goal_id: str, *, team_id: str = "") -> dict:
        goal = self._require_goal(goal_id)
        if team_id:
            goal = self.store.upsert("goal", goal_id, {**goal, "team_id": team_id})
        discussion = await self.start_discussion(
            team_id=str(goal.get("team_id") or ""),
            topic=self._goal_planning_prompt(goal),
            goal_id=goal_id,
            discussion_type="goal_plan",
        )
        work_items = self._extract_draft_work_items(goal, discussion)
        for item in work_items:
            self.store.upsert("work_item", item["id"], item)
        updated = self.store.upsert("goal", goal_id, {
            **goal,
            "status": "planned",
            "plan_discussion_id": discussion["id"],
        })
        self.os.audit.record(
            "opc.goal.planned",
            resource={"type": "opc_goal", "id": goal_id},
            metadata={"discussion_id": discussion["id"], "work_items": len(work_items)},
        )
        return {"goal": updated, "discussion": discussion, "work_items": work_items}

    def approve_goal_plan(self, goal_id: str, work_item_ids: list[str] | None = None) -> dict:
        goal = self._require_goal(goal_id)
        items = [
            item for item in self.store.list("work_item", limit=1000)
            if item.get("goal_id") == goal_id
            and item.get("status") in {"draft", "approved"}
            and (not work_item_ids or item.get("id") in work_item_ids)
        ]
        todos: list[dict] = []
        for item in items:
            todo = self.os.tasks.create_todo({
                "title": item.get("title") or goal.get("objective") or "OPC work item",
                "notes": item.get("notes") or "",
                "priority": goal.get("priority") or 0,
                "tags": ["opc", f"goal:{goal_id}"],
            })
            todos.append(todo)
            self.store.upsert("work_item", item["id"], {
                **item,
                "status": "approved",
                "todo_id": todo.get("todo_id") or "",
            })
        updated = self.store.upsert("goal", goal_id, {**goal, "status": "active"})
        self.os.audit.record(
            "opc.goal.approved",
            resource={"type": "opc_goal", "id": goal_id},
            metadata={"todos": len(todos)},
        )
        return {"goal": updated, "todos": todos}

    async def start_discussion(
        self,
        *,
        team_id: str,
        topic: str,
        goal_id: str = "",
        discussion_type: str = "roundtable",
    ) -> dict:
        topic = str(topic or "").strip()
        if not topic:
            raise ValueError("discussion topic is required")
        team = self._resolve_team(team_id)
        members = list(team.get("member_agents") or [])
        self._validate_agents_exist(members)
        if not members:
            raise ValueError("team has no members")
        responses = await self.gateway.broadcast(members, topic)
        facilitator = str(team.get("facilitator") or members[0])
        summary = await self._summarize(facilitator, topic, responses)
        discussion_id = make_id("opc-disc-")
        item = self.store.upsert("discussion", discussion_id, {
            "team_id": team["id"],
            "goal_id": goal_id,
            "type": discussion_type,
            "topic": topic,
            "participants": members,
            "facilitator": facilitator,
            "responses": responses,
            "summary": summary,
            "status": "completed",
        })
        self.os.audit.record(
            "opc.discussion.completed",
            resource={"type": "opc_discussion", "id": discussion_id},
            metadata={"team_id": team["id"], "goal_id": goal_id, "participants": len(members)},
        )
        return item

    def summarize_discussion(self, discussion_id: str) -> dict:
        item = self.store.get("discussion", discussion_id)
        if item is None:
            raise ValueError(f"unknown discussion: {discussion_id}")
        return {
            "discussion_id": discussion_id,
            "summary": item.get("summary") or "",
            "responses": item.get("responses") or {},
        }

    def _require_goal(self, goal_id: str) -> dict:
        goal = self.store.get("goal", goal_id)
        if goal is None:
            raise ValueError(f"unknown goal: {goal_id}")
        return goal

    def _resolve_team(self, team_id: str) -> dict:
        team_id = str(team_id or "").strip()
        team = self.store.get("team", team_id) if team_id else None
        if team is not None:
            return team
        teams = self.list_teams()
        if teams:
            return teams[0]
        agents = [a["name"] for a in self.os.agents.list() if a.get("name")]
        if not agents:
            raise ValueError("no agents available for OPC team")
        return self.create_team({
            "name": "OPC Core Team",
            "purpose": "Default one-person-company operating team.",
            "member_agents": agents[: min(4, len(agents))],
            "facilitator": agents[0],
        })

    def _validate_agents_exist(self, agent_names: list[str]) -> None:
        known = {a.get("name") for a in self.os.agents.list()}
        missing = [name for name in agent_names if name not in known]
        if missing:
            raise ValueError(f"unknown agent(s): {', '.join(missing)}")

    @staticmethod
    def _normalize_agent_names(value: Any) -> list[str]:
        if isinstance(value, str):
            raw = value.split(",")
        elif isinstance(value, list):
            raw = value
        else:
            raw = []
        result: list[str] = []
        for item in raw:
            name = str(item).strip()
            if name and name not in result:
                result.append(name)
        return result

    @staticmethod
    def _goal_planning_prompt(goal: dict) -> str:
        return (
            "OPC goal planning discussion.\n"
            f"Objective: {goal.get('objective') or ''}\n"
            f"Success criteria: {goal.get('success_criteria') or ''}\n"
            "Each digital employee should respond from their role with concrete work items, "
            "risks, dependencies, and the first next action."
        )

    @staticmethod
    def _extract_draft_work_items(goal: dict, discussion: dict) -> list[dict]:
        summary = str(discussion.get("summary") or "").strip()
        title = f"Advance OPC goal: {goal.get('objective') or 'Goal'}"
        return [{
            "id": make_id("opc-work-"),
            "goal_id": goal["id"],
            "title": title,
            "notes": summary,
            "assigned_agent": str(discussion.get("facilitator") or ""),
            "status": "draft",
            "todo_id": "",
            "source_discussion_id": discussion["id"],
        }]

    async def _summarize(self, facilitator: str, topic: str, responses: dict[str, str]) -> str:
        prompt = (
            "Summarize this OPC team discussion into decisions, risks, and next work items.\n\n"
            f"Topic:\n{topic}\n\n"
            "Responses:\n"
            + "\n\n".join(f"[{name}]\n{text}" for name, text in responses.items())
        )
        try:
            return await self.gateway.execute(facilitator, prompt)
        except Exception:
            await asyncio.sleep(0)
            return "\n\n".join(f"[{name}] {text}" for name, text in responses.items())
