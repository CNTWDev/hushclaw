"""CRM DomainRuntime implementation."""
from __future__ import annotations

from typing import Any

from hushclaw.domains.base import StaticDomainRuntime
from hushclaw.solutions.enterprise.crm.package import (
    CRM_AGENT_DEFINITIONS,
    CRM_MANIFEST,
    CRM_PACKAGE_METADATA,
)
from hushclaw.solutions.enterprise.crm.store import CRMStore
from hushclaw.solutions.enterprise.crm import tools as crm_tools


class CRMDomainRuntime(StaticDomainRuntime):
    def __init__(self) -> None:
        super().__init__(
            CRM_MANIFEST,
            metadata=CRM_PACKAGE_METADATA,
        )
        self._store: CRMStore | None = None

    def bind_memory(self, memory: Any) -> None:
        conn = getattr(memory, "conn", None)
        if conn is None:
            return
        self._store = CRMStore(conn)
        crm_tools.bind_crm_store(self._store)

    def tools(self) -> list[Any]:
        return [
            crm_tools.create_prospect,
            crm_tools.create_lead,
            crm_tools.search_records,
            crm_tools.log_activity,
            crm_tools.record_market_signal,
            crm_tools.score_prospect,
            crm_tools.create_outbound_draft,
            crm_tools.approve_outbound_draft,
            crm_tools.reject_outbound_draft,
            crm_tools.update_opportunity_stage,
            crm_tools.suggest_next_action,
            crm_tools.accept_next_action,
            crm_tools.dismiss_next_action,
            crm_tools.complete_next_action,
        ]

    def agents(self) -> list[dict[str, Any]]:
        return [dict(item) for item in CRM_AGENT_DEFINITIONS]

    @property
    def store(self) -> CRMStore:
        if self._store is None:
            raise RuntimeError("CRM store is unavailable")
        return self._store

    def list_records(self, dataset: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(dataset, limit=limit)

    def create_record(self, dataset: str, data: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        if dataset == "prospect":
            item = self.store.create_prospect(data, actor_id=actor_id)
        elif dataset == "market_signal":
            item = self.store.record_market_signal(data, actor_id=actor_id)
        elif dataset == "outbound_draft":
            item = self.store.create_outbound_draft(data, actor_id=actor_id)
        else:
            item = self.store.upsert(dataset, data, actor_id=actor_id)
        return {"ok": True, "domain_id": "crm", "dataset": dataset, "item": item}

    def list_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.store.events(entity_type=entity_type, entity_id=entity_id, limit=limit)

    def list_work_items(
        self,
        *,
        state_type: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.store.working_state(
            state_type=state_type,
            status=status,
            limit=limit,
        )

    def execute_action(self, action: str, payload: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        if action == "outbound_draft.set_status":
            item = self.store.update_outbound_draft_status(
                str(payload.get("draft_id") or ""),
                str(payload.get("status") or ""),
                actor_id=actor_id,
            )
            if item is None:
                return {"ok": False, "domain_id": "crm", "action": action, "message": "Outbound draft not found."}
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        if action == "next_action.set_status":
            item = self.store.update_working_state_status(
                str(payload.get("state_id") or ""),
                str(payload.get("status") or ""),
                actor_id=actor_id,
            )
            if item is None:
                return {"ok": False, "domain_id": "crm", "action": action, "message": "Next action not found."}
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        if action == "prospect.score":
            item = self.store.score_prospect(
                str(payload.get("prospect_id") or ""),
                fit_score=float(payload.get("fit_score") or 0.0),
                reasoning_summary=str(payload.get("reasoning_summary") or ""),
                actor_id=actor_id,
            )
            return {"ok": True, "domain_id": "crm", "action": action, "item": item}
        return {"ok": False, "domain_id": "crm", "action": action, "message": "Unknown CRM action."}
