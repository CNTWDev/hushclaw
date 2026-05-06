"""Derived time-horizon and stability labels for memory UI/API payloads."""
from __future__ import annotations

import math
import time
from typing import Any


def age_days(epoch: int | float | None, *, now: float | None = None) -> float:
    ts = float(epoch or 0)
    if ts <= 0:
        return 0.0
    ref = time.time() if now is None else float(now)
    return max(0.0, (ref - ts) / 86400.0)


def decayed_weight(
    *,
    score: float = 1.0,
    timestamp: int | float | None,
    decay_rate: float = 0.002,
    now: float | None = None,
) -> float:
    base = max(0.0, min(1.0, float(score or 0.0)))
    if decay_rate <= 0:
        return base
    return base * math.exp(-decay_rate * age_days(timestamp, now=now))


def classify_note(item: dict[str, Any], *, now: float | None = None, decay_rate: float = 0.002) -> dict[str, Any]:
    note_type = str(item.get("note_type") or "fact")
    memory_kind = str(item.get("memory_kind") or "")
    updated = int(item.get("updated_at") or item.get("modified") or item.get("created_at") or item.get("created") or 0)
    recall_count = int(item.get("recall_count") or 0)
    base = min(1.0, 0.55 + min(recall_count, 5) * 0.07)
    if memory_kind == "user_model" or note_type in {"preference"}:
        horizon = "long_term"
        stability = "stable"
        weight = base
    elif note_type in {"belief", "interest"}:
        horizon = "mid_term"
        stability = "consolidating"
        weight = decayed_weight(score=max(base, 0.72), timestamp=updated, decay_rate=decay_rate * 0.35, now=now)
    else:
        horizon = "recent"
        stability = "decaying"
        weight = decayed_weight(score=base, timestamp=updated, decay_rate=decay_rate, now=now)
    return {
        "time_horizon": horizon,
        "stability": stability,
        "age_days": round(age_days(updated, now=now), 2),
        "effective_weight": round(max(0.0, min(1.0, weight)), 4),
    }


def classify_profile_fact(item: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    updated = int(item.get("updated") or 0)
    confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
    return {
        "time_horizon": "long_term",
        "stability": "stable" if confidence >= 0.75 else "consolidating",
        "age_days": round(age_days(updated, now=now), 2),
        "effective_weight": round(confidence, 4),
        "evidence_count": 1,
    }


def classify_belief_model(item: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    updated = int(item.get("updated") or 0)
    last_consolidated = int(item.get("last_consolidated") or 0)
    entries = item.get("entries") or []
    entry_count = len(entries) if isinstance(entries, list) else int(item.get("entry_count") or 0)
    dirty = bool(int(item.get("dirty") or 0))
    evidence_weight = min(1.0, max(0.0, entry_count / 10.0))
    consolidation_weight = 0.5 if dirty else 1.0
    recency_ts = last_consolidated or updated
    weight = decayed_weight(
        score=evidence_weight * consolidation_weight,
        timestamp=recency_ts,
        decay_rate=0.0007,
        now=now,
    )
    return {
        "time_horizon": "mid_term",
        "stability": "consolidating" if dirty else "stable",
        "age_days": round(age_days(recency_ts, now=now), 2),
        "effective_weight": round(max(0.0, min(1.0, weight)), 4),
        "evidence_count": entry_count,
    }


def classify_reflection(item: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    created = int(item.get("created") or 0)
    success = bool(item.get("success"))
    base = 0.72 if success else 0.86
    return {
        "time_horizon": "learning",
        "stability": "stable" if not success else "consolidating",
        "age_days": round(age_days(created, now=now), 2),
        "effective_weight": round(decayed_weight(score=base, timestamp=created, decay_rate=0.0005, now=now), 4),
        "evidence_count": max(1, int(item.get("source_turn_count") or 0)),
    }


def context_taxonomy(*, has_working_state: bool = False) -> dict[str, Any]:
    return {
        "time_horizon": "now",
        "stability": "volatile",
        "age_days": 0.0,
        "effective_weight": 1.0,
        "evidence_count": 1 if has_working_state else 0,
    }
