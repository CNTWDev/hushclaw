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
        self.ensure_default_channels()
        return {
            "employees": self.list_employees(),
            "employee_drafts": self.list_employee_drafts(),
            "skill_recommendations": self.list_skill_recommendations(),
            "teams": self.list_teams(),
            "channels": self.list_channels(),
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

    def list_employee_drafts(self) -> list[dict]:
        return self.store.list("employee_draft")

    def list_skill_recommendations(self) -> list[dict]:
        return self.store.list("skill_recommendation")

    async def draft_employee(self, requirement: str, *, team_id: str = "") -> dict:
        requirement = str(requirement or "").strip()
        if not requirement:
            raise ValueError("employee requirement is required")
        team = self.store.get("team", team_id) if team_id else None
        draft = self._employee_draft_from_requirement(requirement, team=team)
        draft_id = make_id("opc-emp-draft-")
        item = self.store.upsert("employee_draft", draft_id, {
            **draft,
            "requirement": requirement,
            "team_id": str(team_id or ""),
            "status": "draft",
            "created_agent_name": "",
        })
        recommendations = self.recommend_employee_skills(item["id"])
        self.os.audit.record(
            "opc.employee.drafted",
            resource={"type": "opc_employee_draft", "id": item["id"]},
            metadata={"recommendations": len(recommendations)},
        )
        return {**item, "skill_recommendations": recommendations}

    def update_employee_draft(self, draft_id: str, fields: dict[str, Any]) -> dict:
        current = self._require_employee_draft(draft_id)
        allowed = {
            "agent_name",
            "display_name",
            "description",
            "role",
            "team",
            "team_id",
            "reports_to",
            "responsibilities",
            "capabilities",
            "tools",
            "system_prompt",
            "instructions",
            "status",
        }
        updates = {key: value for key, value in (fields or {}).items() if key in allowed}
        return self.store.upsert("employee_draft", draft_id, {**current, **updates})

    def recommend_employee_skills(self, draft_id: str) -> list[dict]:
        draft = self._require_employee_draft(draft_id)
        existing = [
            item for item in self.list_skill_recommendations()
            if item.get("draft_id") == draft_id
        ]
        if existing:
            return existing
        recommendations = self._skill_recommendations_for_draft(draft)
        saved: list[dict] = []
        for rec in recommendations:
            rec_id = make_id("opc-skill-rec-")
            saved.append(self.store.upsert("skill_recommendation", rec_id, {
                **rec,
                "draft_id": draft_id,
                "status": "suggested",
            }))
        return saved

    def approve_employee_skill(self, draft_id: str, recommendation_id: str) -> dict:
        self._require_employee_draft(draft_id)
        item = self.store.get("skill_recommendation", recommendation_id)
        if item is None or item.get("draft_id") != draft_id:
            raise ValueError(f"unknown skill recommendation: {recommendation_id}")
        return self.store.upsert("skill_recommendation", recommendation_id, {
            **item,
            "status": "approved",
        })

    def create_employee_from_draft(self, draft_id: str) -> dict:
        draft = self._require_employee_draft(draft_id)
        if draft.get("created_agent_name"):
            raise ValueError("employee draft has already been created")
        agent_name = self._unique_agent_name(str(draft.get("agent_name") or "employee"))
        tools = list(draft.get("tools") or [])
        self.gateway.create_agent(
            name=agent_name,
            description=str(draft.get("description") or ""),
            system_prompt=str(draft.get("system_prompt") or ""),
            instructions=str(draft.get("instructions") or ""),
            role=str(draft.get("role") or "specialist"),
            team=str(draft.get("team") or ""),
            reports_to=str(draft.get("reports_to") or ""),
            capabilities=list(draft.get("capabilities") or []),
            tools=tools,
        )
        self.sync_employees_from_agents()
        employee = next(
            (item for item in self.list_employees() if item.get("agent_name") == agent_name),
            None,
        )
        if draft.get("team_id"):
            team = self.store.get("team", str(draft.get("team_id") or ""))
            if team is not None:
                members = self._normalize_agent_names(team.get("member_agents") or [])
                if agent_name not in members:
                    self.update_team(team["id"], {**team, "member_agents": [*members, agent_name]})
        updated = self.store.upsert("employee_draft", draft_id, {
            **draft,
            "agent_name": agent_name,
            "status": "created",
            "created_agent_name": agent_name,
        })
        self.os.audit.record(
            "opc.employee.created",
            resource={"type": "opc_employee", "id": employee.get("id") if employee else agent_name},
            metadata={"draft_id": draft_id, "agent_name": agent_name},
        )
        return {
            "draft": updated,
            "employee": employee or {"agent_name": agent_name},
            "skill_recommendations": [
                item for item in self.list_skill_recommendations()
                if item.get("draft_id") == draft_id
            ],
        }

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
        item = self.store.upsert("team", team_id, {
            "name": name,
            "purpose": str(data.get("purpose") or ""),
            "member_agents": member_agents,
            "facilitator": facilitator,
            "status": str(data.get("status") or "active"),
        })
        self.ensure_channel_for_team(item)
        return item

    def update_team(self, team_id: str, fields: dict[str, Any]) -> dict:
        current = self.store.get("team", team_id)
        if current is None:
            raise ValueError(f"unknown team: {team_id}")
        data = {**current, **fields, "id": team_id}
        return self.create_team(data)

    def list_teams(self) -> list[dict]:
        return self.store.list("team")

    def ensure_default_channels(self) -> list[dict]:
        return [self.ensure_channel_for_team(team) for team in self.list_teams()]

    def ensure_channel_for_team(self, team: dict) -> dict:
        channel_id = str(team.get("channel_id") or f"chan-{team['id']}")
        channel = self.store.upsert("channel", channel_id, {
            "name": team.get("name") or "Team",
            "team_id": team["id"],
            "purpose": team.get("purpose") or "",
            "kind": "team",
            "status": "active",
        })
        if team.get("channel_id") != channel_id:
            self.store.upsert("team", team["id"], {**team, "channel_id": channel_id})
        return channel

    def create_channel(self, data: dict[str, Any]) -> dict:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("channel name is required")
        team_id = str(data.get("team_id") or "").strip()
        if team_id and self.store.get("team", team_id) is None:
            raise ValueError(f"unknown team: {team_id}")
        channel_id = str(data.get("id") or "").strip() or make_id("opc-chan-")
        return self.store.upsert("channel", channel_id, {
            "name": name,
            "team_id": team_id,
            "purpose": str(data.get("purpose") or ""),
            "kind": str(data.get("kind") or "team"),
            "status": str(data.get("status") or "active"),
        })

    def list_channels(self) -> list[dict]:
        self.ensure_default_channels()
        return self.store.list("channel")

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

    def get_channel_history(self, channel_id: str, *, limit: int = 100) -> list[dict]:
        self._require_channel(channel_id)
        items = [
            item for item in self.store.list("message", limit=1000)
            if item.get("channel_id") == channel_id
        ]
        return sorted(items, key=lambda item: int(item.get("created") or 0))[-max(1, int(limit)):]

    async def send_channel_message(
        self,
        channel_id: str,
        text: str,
        *,
        goal_id: str = "",
        target: str = "mentioned",
        agent_names: list[str] | None = None,
    ) -> dict:
        channel = self._require_channel(channel_id)
        text = str(text or "").strip()
        if not text:
            raise ValueError("message text is required")
        if goal_id:
            self._require_goal(goal_id)
        user_message = self._append_message(
            channel_id=channel_id,
            sender_type="user",
            text=text,
            goal_id=goal_id,
        )
        agents = self._resolve_message_targets(channel, text, target=target, agent_names=agent_names or [])
        replies: list[dict] = []
        if agents:
            responses = await self.gateway.broadcast(agents, text)
            for name in agents:
                reply = self._append_message(
                    channel_id=channel_id,
                    sender_type="agent",
                    text=str(responses.get(name) or ""),
                    agent_name=name,
                    goal_id=goal_id,
                )
                replies.append(reply)
        self.os.audit.record(
            "opc.channel.message",
            resource={"type": "opc_channel", "id": channel_id},
            metadata={"target_agents": agents, "goal_id": goal_id},
        )
        return {
            "channel": channel,
            "message": user_message,
            "replies": replies,
            "messages": self.get_channel_history(channel_id),
        }

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
        channel = self.ensure_channel_for_team(team)
        sent = await self.send_channel_message(
            channel["id"],
            f"@all {topic}",
            goal_id=goal_id,
            target="all",
        )
        members = list(team.get("member_agents") or [])
        responses = {
            item.get("agent_name"): item.get("text") or ""
            for item in sent.get("replies", [])
            if item.get("agent_name")
        }
        facilitator = str(team.get("facilitator") or members[0])
        summary = await self._summarize(facilitator, topic, responses)
        summary_message = self._append_message(
            channel_id=channel["id"],
            sender_type="system",
            text=summary,
            agent_name=facilitator,
            goal_id=goal_id,
            message_kind="summary",
        )
        discussion_id = make_id("opc-disc-")
        item = self.store.upsert("discussion", discussion_id, {
            "team_id": team["id"],
            "channel_id": channel["id"],
            "goal_id": goal_id,
            "type": discussion_type,
            "topic": topic,
            "participants": members,
            "facilitator": facilitator,
            "responses": responses,
            "summary": summary,
            "summary_message_id": summary_message["id"],
            "status": "completed",
        })
        self.os.audit.record(
            "opc.discussion.completed",
            resource={"type": "opc_discussion", "id": discussion_id},
            metadata={"team_id": team["id"], "goal_id": goal_id, "participants": len(members)},
        )
        return item

    def _append_message(
        self,
        *,
        channel_id: str,
        sender_type: str,
        text: str,
        agent_name: str = "",
        goal_id: str = "",
        message_kind: str = "message",
    ) -> dict:
        message_id = make_id("opc-msg-")
        return self.store.upsert("message", message_id, {
            "channel_id": channel_id,
            "sender_type": sender_type,
            "agent_name": agent_name,
            "goal_id": goal_id,
            "kind": message_kind,
            "text": text,
        })

    def _require_channel(self, channel_id: str) -> dict:
        channel = self.store.get("channel", str(channel_id or "").strip())
        if channel is None:
            raise ValueError(f"unknown channel: {channel_id}")
        return channel

    def _require_employee_draft(self, draft_id: str) -> dict:
        draft = self.store.get("employee_draft", str(draft_id or "").strip())
        if draft is None:
            raise ValueError(f"unknown employee draft: {draft_id}")
        return draft

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

    def _resolve_message_targets(
        self,
        channel: dict,
        text: str,
        *,
        target: str,
        agent_names: list[str],
    ) -> list[str]:
        team = self.store.get("team", str(channel.get("team_id") or ""))
        members = list((team or {}).get("member_agents") or [])
        if not members:
            return []
        target = (target or "mentioned").strip().lower()
        if target == "all" or "@all" in text:
            return members
        explicit = self._normalize_agent_names(agent_names)
        mentioned = [
            name for name in members
            if f"@{name}" in text
        ]
        names = explicit or mentioned
        self._validate_agents_exist(names)
        return [name for name in names if name in members]

    def _validate_agents_exist(self, agent_names: list[str]) -> None:
        known = {a.get("name") for a in self.os.agents.list()}
        missing = [name for name in agent_names if name not in known]
        if missing:
            raise ValueError(f"unknown agent(s): {', '.join(missing)}")

    def _unique_agent_name(self, base: str) -> str:
        existing = {str(agent.get("name") or "") for agent in self.os.agents.list()}
        name = self._slug(base) or "employee"
        if name == "default":
            name = "opc-employee"
        if name not in existing:
            return name
        index = 2
        while f"{name}-{index}" in existing:
            index += 1
        return f"{name}-{index}"

    def _employee_draft_from_requirement(self, requirement: str, *, team: dict | None = None) -> dict:
        lowered = requirement.lower()
        display_name = self._guess_employee_display_name(requirement)
        agent_name = self._slug(display_name)
        capabilities = self._guess_capabilities(lowered)
        tools = self._guess_tools(lowered)
        responsibilities = [
            "Clarify incoming work and identify the expected deliverable.",
            f"Handle work related to: {requirement}",
            "Report progress, risks, and next actions in the OPC channel.",
        ]
        instructions = (
            "You are a digital employee in an OPC organization. Work inside the team channel, "
            "state assumptions clearly, ask for missing inputs, and produce concrete next actions."
        )
        system_prompt = (
            f"You are {display_name}, a digital employee for an OPC team. "
            f"Your job is to help with: {requirement}. "
            "Be operational, concise, and explicit about decisions, risks, and deliverables."
        )
        return {
            "agent_name": agent_name,
            "display_name": display_name,
            "description": requirement[:240],
            "role": "specialist",
            "team": str((team or {}).get("name") or ""),
            "reports_to": str((team or {}).get("facilitator") or ""),
            "responsibilities": responsibilities,
            "capabilities": capabilities,
            "tools": tools,
            "system_prompt": system_prompt,
            "instructions": instructions,
        }

    def _skill_recommendations_for_draft(self, draft: dict) -> list[dict]:
        capabilities = {str(item).lower() for item in draft.get("capabilities") or []}
        base = [
            {
                "name": f"{draft.get('agent_name') or 'employee'}-operating-playbook",
                "title": "Operating Playbook",
                "description": "Reusable checklist for how this employee receives work, produces deliverables, and reports status.",
                "kind": "create",
            }
        ]
        if {"research", "analysis"} & capabilities:
            base.append({
                "name": f"{draft.get('agent_name') or 'employee'}-research-brief",
                "title": "Research Brief",
                "description": "Procedure for collecting facts, comparing options, and summarizing evidence.",
                "kind": "create",
            })
        if {"writing", "content"} & capabilities:
            base.append({
                "name": f"{draft.get('agent_name') or 'employee'}-content-review",
                "title": "Content Review",
                "description": "Checklist for drafting, editing, and reviewing user-facing content.",
                "kind": "create",
            })
        return base[:3]

    @staticmethod
    def _guess_employee_display_name(requirement: str) -> str:
        text = " ".join(str(requirement or "").replace("，", " ").replace("。", " ").split())
        if not text:
            return "OPC Employee"
        lowered = text.lower()
        if any(word in lowered for word in ("market", "竞品", "调研", "research")):
            return "Market Researcher"
        if any(word in lowered for word in ("design", "设计", "brand", "品牌")):
            return "Design Partner"
        if any(word in lowered for word in ("sales", "销售", "outreach", "客户")):
            return "Growth Operator"
        if any(word in lowered for word in ("finance", "财务", "budget")):
            return "Finance Analyst"
        return "OPC Specialist"

    @staticmethod
    def _guess_capabilities(lowered_requirement: str) -> list[str]:
        capabilities = ["execution"]
        mapping = [
            (("market", "research", "调研", "竞品"), "research"),
            (("analysis", "分析", "strategy", "策略"), "analysis"),
            (("write", "content", "文案", "文章"), "writing"),
            (("design", "设计", "brand", "品牌"), "design"),
            (("sales", "销售", "outreach", "客户"), "sales"),
            (("operation", "运营", "process", "流程"), "operations"),
        ]
        for needles, capability in mapping:
            if any(needle in lowered_requirement for needle in needles) and capability not in capabilities:
                capabilities.append(capability)
        return capabilities

    @staticmethod
    def _guess_tools(lowered_requirement: str) -> list[str]:
        tools: list[str] = []
        if any(word in lowered_requirement for word in ("file", "document", "文档", "资料")):
            tools.extend(["read_file", "write_file"])
        if any(word in lowered_requirement for word in ("research", "调研", "竞品", "search")):
            tools.extend(["fetch_url", "read_file"])
        return list(dict.fromkeys(tools))

    @staticmethod
    def _slug(value: str) -> str:
        raw = str(value or "").strip().lower()
        out: list[str] = []
        prev_dash = False
        for ch in raw:
            if ch.isascii() and ch.isalnum():
                out.append(ch)
                prev_dash = False
            elif not prev_dash:
                out.append("-")
                prev_dash = True
        return "".join(out).strip("-")[:48]

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
