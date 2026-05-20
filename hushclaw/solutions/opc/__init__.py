"""OPC one-person-company solution layer.

OPC is a product subsystem above AgentOS. It models digital employees, teams,
goals, and discussions while delegating agent execution to the existing gateway.
"""

from hushclaw.solutions.opc.service import OpcService
from hushclaw.solutions.opc.store import OpcStore

__all__ = ["OpcService", "OpcStore"]
