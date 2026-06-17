"""Scanning helpers for injected prompt/context content."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hushclaw.runtime.threat_patterns import ThreatScan, scan_text, wrap_untrusted_context

_HIGH_RISK_LABELS = {
    "ignore_instructions",
    "system_prompt_request",
    "tool_impersonation",
    "role_impersonation",
    "c2_exfiltration",
}


@dataclass(frozen=True, slots=True)
class InjectedContentPolicy:
    scan_enabled: bool = True
    drop_high_risk: bool = True
    annotate_threat_labels: bool = True


@dataclass(frozen=True, slots=True)
class ScannedContent:
    text: str
    scan: ThreatScan
    dropped: bool = False
    wrapped: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def scan_injected_text(
    content: str,
    *,
    source: str,
    kind: str,
    trusted: bool = False,
    wrap: bool = False,
    policy: InjectedContentPolicy | None = None,
) -> ScannedContent:
    """Scan injected content and optionally wrap it with an instruction boundary."""
    value = str(content or "").strip()
    effective = policy or InjectedContentPolicy()
    if not value:
        return ScannedContent(text="", scan=ThreatScan(labels=()))

    scan = scan_text(value) if effective.scan_enabled else ThreatScan(labels=())
    labels = tuple(scan.labels)
    high_risk = any(label in _HIGH_RISK_LABELS for label in labels)
    metadata = {
        "source": source,
        "kind": kind,
        "trusted": trusted,
        "threat_labels": list(labels) if effective.annotate_threat_labels else [],
        "high_risk": high_risk,
    }
    if high_risk and effective.drop_high_risk:
        return ScannedContent(
            text=(
                f"[{kind}] Content from {source} was withheld because it matched "
                "high-risk prompt injection patterns."
            ),
            scan=scan,
            dropped=True,
            wrapped=False,
            metadata=metadata,
        )
    if wrap:
        wrapped, _ = wrap_untrusted_context(
            value,
            source=source,
            kind=kind,
            trusted=trusted,
        )
        return ScannedContent(
            text=wrapped,
            scan=scan,
            dropped=False,
            wrapped=True,
            metadata=metadata,
        )
    return ScannedContent(
        text=value,
        scan=scan,
        dropped=False,
        wrapped=False,
        metadata=metadata,
    )
