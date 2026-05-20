"""Enterprise solution default business domain catalog."""
from __future__ import annotations

from hushclaw.domains.base import DomainManifest, StaticDomainRuntime
from hushclaw.domains.registry import DomainRegistry
from hushclaw.solutions.enterprise.crm import CRMDomainRuntime


def enterprise_domain_registry() -> DomainRegistry:
    """Return enterprise business domains.

    Enterprise Foundation lives in the Enterprise distro/directory substrate.
    This catalog is only for installable business solution packages.
    """
    return DomainRegistry([
        CRMDomainRuntime(),
        StaticDomainRuntime(
            DomainManifest(
                id="hr",
                name="People Ops",
                description="People operations domain for future hiring, onboarding, performance, and learning workflows.",
                module_type="business_domain",
                platform_requirements=("directory", "rbac", "audit"),
                capabilities=("people_operations", "candidate_screening", "onboarding"),
                entity_types=("hr.candidate", "hr.employee", "hr.position"),
                tools=("hr.search_people", "hr.create_onboarding_task"),
                agents=("hr.recruiting_assistant", "hr.onboarding_coach"),
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
                module_type="business_domain",
                platform_requirements=("directory", "rbac", "audit"),
                capabilities=("budget_tracking", "approval_workflows", "spend_analysis"),
                entity_types=("finance.budget", "finance.expense", "finance.contract"),
                tools=("finance.search_budgets", "finance.review_expense"),
                agents=("finance_reviewer", "budget_analyst"),
                required_permissions=("finance.read", "finance.approve"),
                status="planned",
            ),
            metadata={"phase": "planned", "kind": "business_domain", "solution": "enterprise"},
        ),
    ])
