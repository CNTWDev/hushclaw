"""Domain registry for Enterprise distro business capability packages."""
from __future__ import annotations

from typing import Any

from hushclaw.domains.base import DomainManifest, DomainRuntime, StaticDomainRuntime


class DomainRegistry:
    """In-memory v1 registry for domain runtimes.

    The registry is deliberately generic: it stores domain manifests and runtime
    adapters, but it never interprets business-specific entities.
    """

    def __init__(self, domains: list[DomainRuntime] | None = None) -> None:
        self._domains: dict[str, DomainRuntime] = {}
        for domain in domains or []:
            self.register(domain)

    @classmethod
    def default_enterprise(cls) -> "DomainRegistry":
        return cls([
            StaticDomainRuntime(
                DomainManifest(
                    id="crm",
                    name="CRM",
                    description="Customer, lead, opportunity, and follow-up workspace domain.",
                    capabilities=("customer_management", "lead_scoring", "opportunity_tracking"),
                    entity_types=("crm.lead", "crm.account", "crm.contact", "crm.opportunity", "crm.activity"),
                    tools=("crm.search_customers", "crm.create_followup_task", "crm.update_opportunity"),
                    agents=("crm.lead_qualifier", "crm.account_researcher", "crm.deal_coach"),
                    ui_entries=("enterprise.domains.crm",),
                    required_permissions=("crm.read", "crm.write"),
                    status="planned",
                ),
                metadata={"phase": "planned", "kind": "business_domain"},
            ),
            StaticDomainRuntime(
                DomainManifest(
                    id="hr",
                    name="HR",
                    description="People operations domain for future hiring, onboarding, performance, and learning workflows.",
                    capabilities=("people_operations", "candidate_screening", "onboarding"),
                    entity_types=("hr.candidate", "hr.employee", "hr.position"),
                    tools=("hr.search_people", "hr.create_onboarding_task"),
                    agents=("hr.recruiting_assistant", "hr.onboarding_coach"),
                    ui_entries=("enterprise.domains.hr",),
                    required_permissions=("hr.read", "hr.write"),
                    status="planned",
                ),
                metadata={"phase": "planned", "kind": "business_domain"},
            ),
            StaticDomainRuntime(
                DomainManifest(
                    id="finance",
                    name="Finance",
                    description="Finance operations domain for budget, approval, contract, and spend workflows.",
                    capabilities=("budget_tracking", "approval_workflows", "spend_analysis"),
                    entity_types=("finance.budget", "finance.expense", "finance.contract"),
                    tools=("finance.search_budgets", "finance.review_expense"),
                    agents=("finance_reviewer", "budget_analyst"),
                    ui_entries=("enterprise.domains.finance",),
                    required_permissions=("finance.read", "finance.approve"),
                    status="planned",
                ),
                metadata={"phase": "planned", "kind": "business_domain"},
            ),
        ])

    def register(self, domain: DomainRuntime) -> None:
        manifest = domain.manifest()
        self._domains[manifest.id] = domain

    def get(self, domain_id: str) -> DomainRuntime | None:
        return self._domains.get(domain_id)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "manifest": domain.manifest().to_dict(),
                "status": domain.status(),
            }
            for domain in sorted(self._domains.values(), key=lambda item: item.manifest().id)
        ]

    def manifest(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        return domain.manifest().to_dict() if domain else {}

    def status(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        return domain.status() if domain else {}
