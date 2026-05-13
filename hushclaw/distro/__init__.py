"""hushclaw.distro — Distro contract and built-in distribution registry."""
from hushclaw.distro.base import DistroAdapter, DistroManifest
from hushclaw.distro.runtime import DistroRuntime, RuntimeBundle
from hushclaw.distro.personal import PersonalDistro
from hushclaw.distro.team import TeamDistro
from hushclaw.distro.enterprise import EnterpriseDistro

DistroRuntime.register(PersonalDistro())
DistroRuntime.register(TeamDistro())
DistroRuntime.register(EnterpriseDistro())

__all__ = [
    "DistroManifest",
    "DistroAdapter",
    "DistroRuntime",
    "RuntimeBundle",
    "PersonalDistro",
    "TeamDistro",
    "EnterpriseDistro",
]
