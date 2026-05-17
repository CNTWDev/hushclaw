"""Lightweight context assembly trace records."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ContextTraceItem:
    source: str
    tier: str
    hit: bool
    chars: int = 0
    budget_tokens: int = 0
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tier": self.tier,
            "hit": self.hit,
            "chars": self.chars,
            "budget_tokens": self.budget_tokens,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "metadata": dict(self.metadata),
        }


class ContextTrace:
    """Per-assembler trace of the latest context assembly."""

    def __init__(self) -> None:
        self.items: list[ContextTraceItem] = []

    def reset(self) -> None:
        self.items = []

    def add(
        self,
        source: str,
        *,
        tier: str,
        content: str = "",
        hit: bool | None = None,
        budget_tokens: int = 0,
        elapsed_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = content or ""
        self.items.append(
            ContextTraceItem(
                source=source,
                tier=tier,
                hit=bool(text) if hit is None else bool(hit),
                chars=len(text),
                budget_tokens=max(0, int(budget_tokens or 0)),
                elapsed_ms=max(0.0, float(elapsed_ms or 0.0)),
                metadata=dict(metadata or {}),
            )
        )

    def summary(self) -> dict[str, Any]:
        total_chars = sum(item.chars for item in self.items)
        hits = sum(1 for item in self.items if item.hit)
        return {
            "items": [item.to_dict() for item in self.items],
            "total_chars": total_chars,
            "hits": hits,
            "misses": len(self.items) - hits,
        }
