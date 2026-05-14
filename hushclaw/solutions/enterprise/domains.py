"""Enterprise solution default domain catalog."""
from __future__ import annotations

from hushclaw.domains.base import DomainManifest, StaticDomainRuntime
from hushclaw.domains.registry import DomainRegistry
from hushclaw.solutions.enterprise.crm import CRMDomainRuntime


def enterprise_domain_registry() -> DomainRegistry:
    """Return the default enterprise business domain catalog.

    These manifests are owned by the Enterprise solution, not by AgentOS kernel.
    They establish installable module entries before full CRM/HR/Finance runtimes
    are implemented.
    """
    return DomainRegistry([
        StaticDomainRuntime(
            DomainManifest(
                id="people_foundation",
                name="People Foundation",
                description="Organization, members, teams, positions, reporting lines, and identity references for enterprise modules.",
                module_type="foundation",
                capabilities=("org_directory", "member_directory", "team_directory", "reporting_graph"),
                entity_types=(
                    "enterprise.org_unit",
                    "enterprise.position",
                    "enterprise.member",
                    "enterprise.team",
                    "enterprise.role_assignment",
                ),
                admin_routes=("/enterprise/admin#organization", "/enterprise/admin#access"),
                workspace_routes=(),
                required_permissions=("directory.manage", "role.manage"),
                status="available",
                category="foundation",
            ),
            installed=True,
            configured=True,
            enabled=True,
            metadata={"phase": "foundation", "kind": "foundation_module", "solution": "enterprise", "scope": "org"},
        ),
        CRMDomainRuntime(),
        StaticDomainRuntime(
            DomainManifest(
                id="hr",
                name="HR",
                description="People operations domain for future hiring, onboarding, performance, and learning workflows.",
                module_type="business_domain",
                dependencies=("people_foundation",),
                capabilities=("people_operations", "candidate_screening", "onboarding"),
                entity_types=("hr.candidate", "hr.employee", "hr.position"),
                tools=("hr.search_people", "hr.create_onboarding_task"),
                agents=("hr.recruiting_assistant", "hr.onboarding_coach"),
                admin_routes=("/enterprise/admin#domain:hr",),
                workspace_routes=("/enterprise#hr",),
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
                module_type="business_domain",
                dependencies=("people_foundation",),
                capabilities=("budget_tracking", "approval_workflows", "spend_analysis"),
                entity_types=("finance.budget", "finance.expense", "finance.contract"),
                tools=("finance.search_budgets", "finance.review_expense"),
                agents=("finance_reviewer", "budget_analyst"),
                admin_routes=("/enterprise/admin#domain:finance",),
                workspace_routes=("/enterprise#finance",),
                ui_entries=("enterprise.domains.finance",),
                required_permissions=("finance.read", "finance.approve"),
                status="planned",
            ),
            metadata={"phase": "planned", "kind": "business_domain", "solution": "enterprise"},
        ),
    ])
