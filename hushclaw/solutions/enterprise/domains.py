"""Enterprise solution default domain catalog."""
from __future__ import annotations

from hushclaw.domains.base import DomainManifest, StaticDomainRuntime
from hushclaw.domains.registry import DomainRegistry


def enterprise_domain_registry() -> DomainRegistry:
    """Return the default enterprise business domain catalog.

    These manifests are owned by the Enterprise solution, not by AgentOS kernel.
    They establish installable module entries before full CRM/HR/Finance runtimes
    are implemented.
    """
    return DomainRegistry([
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
            metadata={"phase": "planned", "kind": "business_domain", "solution": "enterprise"},
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
            metadata={"phase": "planned", "kind": "business_domain", "solution": "enterprise"},
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
            metadata={"phase": "planned", "kind": "business_domain", "solution": "enterprise"},
        ),
    ])
