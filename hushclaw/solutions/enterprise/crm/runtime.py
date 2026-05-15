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
                capabilities=("customer_facts", "lead_capture", "activity_events", "next_action_suggestions"),
                entity_types=("crm.lead", "crm.account", "crm.contact", "crm.opportunity", "crm.activity"),
                tools=(
                    "crm.create_lead",
                    "crm.search_records",
                    "crm.log_activity",
                    "crm.update_opportunity_stage",
                    "crm.suggest_next_action",
                    "crm.accept_next_action",
                    "crm.dismiss_next_action",
                    "crm.complete_next_action",
                ),
                agents=("crm.lead_qualifier", "crm.account_researcher", "crm.deal_coach"),
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
            crm_tools.create_lead,
            crm_tools.search_records,
            crm_tools.log_activity,
            crm_tools.update_opportunity_stage,
            crm_tools.suggest_next_action,
            crm_tools.accept_next_action,
            crm_tools.dismiss_next_action,
            crm_tools.complete_next_action,
        ]

    def agents(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "crm.lead_qualifier",
                "description": "Qualifies new leads using CRM facts and recent activity events.",
                "domain_id": "crm",
                "visibility": "employee_visible",
                "role": "specialist",
                "team": "CRM",
                "capabilities": ["lead_capture", "lead_scoring", "next_action"],
                "tools": [
                    "crm.search_records",
                    "crm.log_activity",
                    "crm.suggest_next_action",
                ],
                "instructions": (
                    "You are the CRM lead qualifier. Use CRM facts and recent CRM events to "
                    "qualify leads, identify missing information, and propose the next sales action. "
                    "Do not invent CRM records; use CRM tools for reads and mutations."
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
                    "crm.suggest_next_action",
                ],
                "instructions": (
                    "You are the CRM account researcher. Summarize customer/account facts from CRM "
                    "records and events, call out gaps, and recommend what the employee should verify."
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
