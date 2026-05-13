"""Enterprise solution package.

Enterprise owns the org directory, admin/workspace shells, and default business
domain catalog. AgentOS kernel owns only the generic runtime contracts.
"""

from hushclaw.solutions.enterprise.domains import enterprise_domain_registry

__all__ = ["enterprise_domain_registry"]
