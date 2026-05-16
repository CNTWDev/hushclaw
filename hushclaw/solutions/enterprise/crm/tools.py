"""Agent-facing CRM tools.

These tools expose lightweight CRM facts and events. They are intentionally
small: AgentOS plans and reasons, while CRM stores domain facts and events.
"""
from __future__ import annotations

import json
from typing import Any

from hushclaw.runtime.principal import current_principal
from hushclaw.tools.base import ToolResult, tool

_crm_store = None


def bind_crm_store(store) -> None:
    global _crm_store
    _crm_store = store


def _store():
    if _crm_store is None:
        raise RuntimeError("CRM domain store is unavailable")
    return _crm_store


@tool(
    name="crm.create_lead",
    description="Create a lightweight CRM lead fact and append a CRM event.",
    mutating=True,
)
def create_lead(
    name: str,
    source: str = "",
    owner_id: str = "",
    team_id: str = "",
    notes: str = "",
) -> ToolResult:
    item = _store().upsert(
        "lead",
        {
            "name": name,
            "source": source,
            "owner_id": owner_id,
            "team_id": team_id,
            "notes": notes,
            "status": "new",
        },
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.create_prospect",
    description="Create or update a potential partner prospect with fit and source metadata.",
    mutating=True,
)
def create_prospect(
    name: str,
    website: str = "",
    industry: str = "",
    region: str = "",
    source: str = "",
    owner_id: str = "",
    reasoning_summary: str = "",
    fit_score: float = 0.0,
) -> ToolResult:
    item = _store().create_prospect(
        {
            "name": name,
            "website": website,
            "industry": industry,
            "region": region,
            "source": source,
            "owner_id": owner_id,
            "reasoning_summary": reasoning_summary,
            "fit_score": fit_score,
        },
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.search_records",
    description="Search CRM accounts, contacts, leads, opportunities, activities, and pipeline stages.",
    parallel_safe=True,
)
def search_records(query: str = "", entity_type: str = "", limit: int = 20) -> ToolResult:
    items = _store().search(query, entity_type=entity_type, limit=limit)
    return ToolResult.ok(json.dumps(items, ensure_ascii=False))


@tool(
    name="crm.log_activity",
    description="Log a lightweight CRM activity against an entity.",
    mutating=True,
)
def log_activity(
    subject: str,
    entity_type: str,
    entity_id: str,
    body: str = "",
    owner_id: str = "",
) -> ToolResult:
    activity = _store().upsert(
        "activity",
        {
            "subject": subject,
            "related_entity_type": entity_type,
            "related_entity_id": entity_id,
            "body": body,
            "owner_id": owner_id,
        },
        actor_id=current_principal().principal_id,
    )
    _store().append_event(
        entity_type,
        entity_id,
        "crm.activity.logged",
        {"activity_id": activity["id"], "subject": subject, "body": body},
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(activity, ensure_ascii=False))


@tool(
    name="crm.record_market_signal",
    description="Record a market signal for a prospect, such as news, hiring, funding, product launch, or website change.",
    mutating=True,
)
def record_market_signal(
    title: str,
    summary: str = "",
    source: str = "",
    url: str = "",
    signal_type: str = "market",
    prospect_id: str = "",
    confidence: float = 0.0,
) -> ToolResult:
    item = _store().record_market_signal(
        {
            "title": title,
            "summary": summary,
            "source": source,
            "url": url,
            "signal_type": signal_type,
            "prospect_id": prospect_id,
            "confidence": confidence,
        },
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.score_prospect",
    description="Update a prospect's fit score and reasoning summary.",
    mutating=True,
)
def score_prospect(prospect_id: str, fit_score: float, reasoning_summary: str = "") -> ToolResult:
    item = _store().score_prospect(
        prospect_id,
        fit_score=fit_score,
        reasoning_summary=reasoning_summary,
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.create_outbound_draft",
    description="Create an outbound email/message draft for a prospect. This does not send it.",
    mutating=True,
)
def create_outbound_draft(
    prospect_id: str,
    subject: str,
    body: str,
    channel: str = "email",
    owner_id: str = "",
) -> ToolResult:
    item = _store().create_outbound_draft(
        {
            "prospect_id": prospect_id,
            "subject": subject,
            "body": body,
            "channel": channel,
            "owner_id": owner_id,
        },
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.approve_outbound_draft",
    description="Approve an outbound draft. Approval is required before any future sending tool may send it.",
    mutating=True,
)
def approve_outbound_draft(draft_id: str) -> ToolResult:
    item = _store().update_outbound_draft_status(
        draft_id,
        "approved",
        actor_id=current_principal().principal_id,
    )
    if item is None:
        return ToolResult.error(f"CRM outbound draft not found: {draft_id}")
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.reject_outbound_draft",
    description="Reject an outbound draft.",
    mutating=True,
)
def reject_outbound_draft(draft_id: str) -> ToolResult:
    item = _store().update_outbound_draft_status(
        draft_id,
        "rejected",
        actor_id=current_principal().principal_id,
    )
    if item is None:
        return ToolResult.error(f"CRM outbound draft not found: {draft_id}")
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.update_opportunity_stage",
    description="Update an opportunity's stage and append a stage-change event.",
    mutating=True,
)
def update_opportunity_stage(opportunity_id: str, stage: str, probability: float = 0.0) -> ToolResult:
    existing = _store().get("opportunity", opportunity_id) or {"id": opportunity_id, "name": opportunity_id}
    item = _store().upsert(
        "opportunity",
        {**existing, "stage": stage, "probability": probability},
        actor_id=current_principal().principal_id,
    )
    _store().append_event(
        "opportunity",
        opportunity_id,
        "crm.opportunity.stage_changed",
        {"stage": stage, "probability": probability},
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.suggest_next_action",
    description="Return a lightweight next-action suggestion for a CRM entity based on recent events.",
    parallel_safe=True,
)
def suggest_next_action(entity_type: str, entity_id: str) -> ToolResult:
    payload: dict[str, Any] = _store().suggest_next_action(
        entity_type,
        entity_id,
        actor_id=current_principal().principal_id,
    )
    return ToolResult.ok(json.dumps(payload, ensure_ascii=False))


@tool(
    name="crm.accept_next_action",
    description="Mark a CRM next-action suggestion as accepted by the current actor.",
    mutating=True,
)
def accept_next_action(state_id: str) -> ToolResult:
    item = _store().update_working_state_status(
        state_id,
        "accepted",
        actor_id=current_principal().principal_id,
    )
    if item is None:
        return ToolResult.error(f"CRM next action not found: {state_id}")
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.dismiss_next_action",
    description="Dismiss a CRM next-action suggestion.",
    mutating=True,
)
def dismiss_next_action(state_id: str) -> ToolResult:
    item = _store().update_working_state_status(
        state_id,
        "dismissed",
        actor_id=current_principal().principal_id,
    )
    if item is None:
        return ToolResult.error(f"CRM next action not found: {state_id}")
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))


@tool(
    name="crm.complete_next_action",
    description="Mark a CRM next-action suggestion as completed.",
    mutating=True,
)
def complete_next_action(state_id: str) -> ToolResult:
    item = _store().update_working_state_status(
        state_id,
        "completed",
        actor_id=current_principal().principal_id,
    )
    if item is None:
        return ToolResult.error(f"CRM next action not found: {state_id}")
    return ToolResult.ok(json.dumps(item, ensure_ascii=False))
