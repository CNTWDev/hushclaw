"""Typed runtime context passed to tools and runtime policy checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolRuntimeContext:
    """Shared per-loop runtime objects used by tool execution.

    The core fields provide a typed home for the most common runtime
    dependencies. ``extras`` keeps backward compatibility with legacy
    underscore-prefixed injections while the codebase migrates.
    """

    session_id: str
    config: Any = None
    memory: Any = None
    registry: Any = None
    gateway: Any = None
    loop: Any = None
    skill_registry: Any = None
    skill_manager: Any = None
    scheduler: Any = None
    browser: Any = None
    handover_registry: dict[str, Any] | None = None
    output_dir: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        mapping = {
            "_session_id": self.session_id,
            "_config": self.config,
            "_memory_store": self.memory,
            "_registry": self.registry,
            "_gateway": self.gateway,
            "_loop": self.loop,
            "_skill_registry": self.skill_registry,
            "_skill_manager": self.skill_manager,
            "_scheduler": self.scheduler,
            "_browser": self.browser,
            "_handover_registry": self.handover_registry,
            "_output_dir": self.output_dir,
        }
        if key in mapping:
            value = mapping[key]
            if value is not None:
                return value
            # Field is None — fall through to extras so callers can inject a fallback.
        return self.extras.get(key, default)

    def legacy_items(self) -> dict[str, Any]:
        return {
            "_session_id": self.session_id,
            "_config": self.config,
            "_memory_store": self.memory,
            "_registry": self.registry,
            "_gateway": self.gateway,
            "_loop": self.loop,
            "_skill_registry": self.skill_registry,
            "_skill_manager": self.skill_manager,
            "_scheduler": self.scheduler,
            "_browser": self.browser,
            "_handover_registry": self.handover_registry,
            "_output_dir": self.output_dir,
            **self.extras,
        }
