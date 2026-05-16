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
                dependencies=("people_foundation",),
                capabilities=(
                    "customer_facts",
                    "partner_discovery",
                    "market_signal_tracking",
                    "lead_capture",
                    "activity_events",
                    "outbound_draft_approval",
                    "next_action_suggestions",
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
