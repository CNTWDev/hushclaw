"""CRM DomainRuntime implementation."""
from __future__ import annotations

from typing import Any

from hushclaw.domains.base import DomainManifest, StaticDomainRuntime
from hushclaw.solutions.enterprise.crm.store import CRMStore
from hushclaw.solutions.enterprise.crm import tools as crm_tools


class CRMDomainRuntime(StaticDomainRuntime):
    def __init__(self) -> None:
        super().__init__(
            DomainManifest(
                id="crm",
                name="CRM",
                description="AgentOS-driven customer, lead, opportunity, and activity domain.",
                module_type="business_domain",
                platform_requirements=("directory", "rbac", "audit"),
                capabilities=(
                    "customer_facts",
                    "partner_discovery",
                    "market_signal_tracking",
                    "lead_capture",
                    "activity_events",
                    "outbound_draft_approval",
                    "next_action_suggestions",
                ),
                datasets=(
                    {
                        "id": "prospect",
                        "entity_type": "crm.prospect",
                        "description": "Potential partner or customer discovered by employees or CRM agents.",
                        "owner": "crm",
                    },
                    {
                        "id": "market_signal",
                        "entity_type": "crm.market_signal",
                        "description": "External or internal signal linked to a prospect, account, or market.",
                        "owner": "crm",
                    },
                    {
                        "id": "outbound_draft",
                        "entity_type": "crm.outbound_draft",
                        "description": "Human-approved outbound message draft; CRM does not send it automatically.",
                        "owner": "crm",
                    },
                    {"id": "lead", "entity_type": "crm.lead", "description": "Lightweight inbound lead.", "owner": "crm"},
                    {"id": "account", "entity_type": "crm.account", "description": "Customer or partner account.", "owner": "crm"},
                    {"id": "contact", "entity_type": "crm.contact", "description": "Person attached to an account.", "owner": "crm"},
                    {"id": "opportunity", "entity_type": "crm.opportunity", "description": "Revenue or partnership opportunity.", "owner": "crm"},
                    {"id": "activity", "entity_type": "crm.activity", "description": "Interaction or follow-up activity.", "owner": "crm"},
                ),
                event_types=(
                    "crm.prospect.created",
                    "crm.prospect.scored",
                    "crm.market_signal.created",
                    "crm.market_signal.linked",
                    "crm.outbound_draft.created",
                    "crm.outbound_draft.approved",
                    "crm.outbound_draft.rejected",
                    "crm.activity.logged",
                    "crm.opportunity.stage_changed",
                    "agent.next_action.suggested",
                    "agent.next_action.accepted",
                    "agent.next_action.dismissed",
                    "agent.next_action.completed",
                ),
                workflows=(
                    {
                        "id": "partner_discovery",
                        "description": "Discover prospects, record market signals, score fit, and prepare a human-approved outreach draft.",
                        "states": ["discovered", "scored", "drafted", "approved", "followed_up"],
                    },
                    {
                        "id": "next_action_loop",
                        "description": "Create suggested next actions from CRM events and let employees accept, dismiss, or complete them.",
                        "states": ["suggested", "accepted", "dismissed", "completed"],
                    },
                ),
                policies=(
                    {
                        "id": "crm.outbound_requires_approval",
                        "description": "Outbound messages remain drafts until approved by a human.",
                        "default": True,
                    },
                    {
                        "id": "crm.domain_tool_isolation",
                        "description": "CRM tools are callable by CRM-owned agents or enterprise/domain admins only.",
                        "default": True,
                    },
                ),
                entity_types=(
                    "crm.prospect",
                    "crm.market_signal",
                    "crm.outbound_draft",
                    "crm.lead",
                    "crm.account",
                    "crm.contact",
                    "crm.opportunity",
                    "crm.activity",
                ),
                tools=(
                    "crm.create_prospect",
                    "crm.create_lead",
                    "crm.search_records",
                    "crm.log_activity",
                    "crm.record_market_signal",
                    "crm.score_prospect",
                    "crm.create_outbound_draft",
                    "crm.approve_outbound_draft",
                    "crm.reject_outbound_draft",
                    "crm.update_opportunity_stage",
                    "crm.suggest_next_action",
                    "crm.accept_next_action",
                    "crm.dismiss_next_action",
                    "crm.complete_next_action",
                ),
                agents=(
                    "crm.market_scout",
                    "crm.partner_qualifier",
                    "crm.account_researcher",
                    "crm.followup_planner",
                    "crm.outbound_writer",
                    "crm.deal_coach",
                ),
                admin_routes=("/enterprise/admin#domain:crm",),
                workspace_routes=("/enterprise#crm",),
                ui_entries=("enterprise.domains.crm",),
                ui_facets=(
                    {"id": "strategy_console", "surface": "admin", "route": "/enterprise/admin#domain:crm"},
                    {"id": "partner_pipeline", "surface": "workspace", "route": "/enterprise#crm"},
                    {"id": "approval_queue", "surface": "workspace", "route": "/enterprise#crm"},
                ),
                required_permissions=("crm.read", "crm.write", "crm.admin"),
                status="available",
            ),
            metadata={"phase": "v1", "kind": "business_domain", "solution": "enterprise"},
        )
        self._store: CRMStore | None = None

    def bind_memory(self, memory: Any) -> None:
        conn = getattr(memory, "conn", None)
        if conn is None:
            return
        self._store = CRMStore(conn)
        crm_tools.bind_crm_store(self._store)

    def tools(self) -> list[Any]:
        return [
            crm_tools.create_prospect,
            crm_tools.create_lead,
            crm_tools.search_records,
            crm_tools.log_activity,
            crm_tools.record_market_signal,
            crm_tools.score_prospect,
            crm_tools.create_outbound_draft,
            crm_tools.approve_outbound_draft,
            crm_tools.reject_outbound_draft,
            crm_tools.update_opportunity_stage,
            crm_tools.suggest_next_action,
            crm_tools.accept_next_action,
            crm_tools.dismiss_next_action,
            crm_tools.complete_next_action,
        ]

    def agents(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "crm.market_scout",
                "description": "Discovers potential partners from target markets and public web signals.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["partner_discovery", "market_signal_tracking", "prospect_creation"],
                "tools": [
                    "crm.search_records",
                    "crm.create_prospect",
                    "crm.record_market_signal",
                    "crm.suggest_next_action",
                    "fetch_url",
                    "jina_read",
                ],
                "instructions": (
                    "You are the CRM market scout. Use the CRM strategy config and public web research "
                    "to identify potential partners, record market signals, and create prospects. "
                    "Do not send outbound messages."
                ),
            },
            {
                "name": "crm.partner_qualifier",
                "description": "Scores prospects against the ideal partner profile and explains fit.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["partner_fit_scoring", "prospect_qualification", "next_action"],
                "tools": [
                    "crm.search_records",
                    "crm.score_prospect",
                    "crm.suggest_next_action",
                ],
                "instructions": (
                    "You are the CRM partner qualifier. Score prospects against target markets and "
                    "ideal partner profile. Explain strong signals, weak signals, risks, and next actions."
                ),
            },
            {
                "name": "crm.account_researcher",
                "description": "Builds account context from CRM records and activity history.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["account_context", "activity_review", "customer_research"],
                "tools": [
                    "crm.search_records",
                    "crm.record_market_signal",
                    "crm.suggest_next_action",
                    "fetch_url",
                    "jina_read",
                ],
                "instructions": (
                    "You are the CRM account researcher. Summarize customer/account facts from CRM "
                    "records and events, call out gaps, and recommend what the employee should verify."
                ),
            },
            {
                "name": "crm.followup_planner",
                "description": "Reviews prospects, signals, and activity history to plan follow-up.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["followup_planning", "next_action", "activity_review"],
                "tools": [
                    "crm.search_records",
                    "crm.log_activity",
                    "crm.suggest_next_action",
                ],
                "instructions": (
                    "You are the CRM follow-up planner. Review prospects, recent signals, drafts, and "
                    "activity history. Generate practical next actions and keep CRM records current."
                ),
            },
            {
                "name": "crm.outbound_writer",
                "description": "Creates outbound drafts for approved human review; it never sends messages.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["outbound_draft_generation", "partner_messaging"],
                "tools": [
                    "crm.search_records",
                    "crm.create_outbound_draft",
                ],
                "instructions": (
                    "You are the CRM outbound writer. Generate concise outreach drafts for prospects. "
                    "Never send messages. Drafts must remain pending until a human approves them."
                ),
            },
            {
                "name": "crm.deal_coach",
                "description": "Reviews opportunity stage, risk, and next customer commitment.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["opportunity_review", "risk_signal", "next_action"],
                "tools": [
                    "crm.search_records",
                    "crm.log_activity",
                    "crm.update_opportunity_stage",
                    "crm.suggest_next_action",
                ],
                "instructions": (
                    "You are the CRM deal coach. Review opportunities, recent activities, and stages. "
                    "Identify risks, suggest next commitments, and update CRM only through CRM tools."
                ),
            },
        ]

    @property
    def store(self) -> CRMStore:
        if self._store is None:
            raise RuntimeError("CRM store is unavailable")
        return self._store

    def list_records(self, dataset: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(dataset, limit=limit)

    def create_record(self, dataset: str, data: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        if dataset == "prospect":
            item = self.store.create_prospect(data, actor_id=actor_id)
        elif dataset == "market_signal":
            item = self.store.record_market_signal(data, actor_id=actor_id)
        elif dataset == "outbound_draft":
            item = self.store.create_outbound_draft(data, actor_id=actor_id)
        else:
            item = self.store.upsert(dataset, data, actor_id=actor_id)
        return {"ok": True, "domain_id": "crm", "dataset": dataset, "item": item}

    def list_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.store.events(entity_type=entity_type, entity_id=entity_id, limit=limit)

    def list_work_items(
        self,
        *,
        state_type: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.store.working_state(
            state_type=state_type,
            status=status,
            limit=limit,
        )

    def execute_action(self, action: str, payload: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        if action == "outbound_draft.set_status":
            item = self.store.update_outbound_draft_status(
                str(payload.get("draft_id") or ""),
                str(payload.get("status") or ""),
                actor_id=actor_id,
            )
            if item is None:
                return {"ok": False, "domain_id": "crm", "action": action, "message": "Outbound draft not found."}
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        if action == "next_action.set_status":
            item = self.store.update_working_state_status(
                str(payload.get("state_id") or ""),
                str(payload.get("status") or ""),
                actor_id=actor_id,
            )
            if item is None:
                return {"ok": False, "domain_id": "crm", "action": action, "message": "Next action not found."}
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        if action == "prospect.score":
            item = self.store.score_prospect(
                str(payload.get("prospect_id") or ""),
                fit_score=float(payload.get("fit_score") or 0.0),
                reasoning_summary=str(payload.get("reasoning_summary") or ""),
                actor_id=actor_id,
            )
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        return {"ok": False, "domain_id": "crm", "action": action, "message": "Unknown CRM action."}
