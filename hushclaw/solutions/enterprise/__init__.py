"""Enterprise solution package.

Internal business-domain catalog prototypes. AgentOS kernel owns only the
generic runtime contracts.
"""

from hushclaw.solutions.enterprise.domains import enterprise_domain_registry

__all__ = ["enterprise_domain_registry"]
