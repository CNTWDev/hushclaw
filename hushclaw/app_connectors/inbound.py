"""Unified inbound event automation for app connectors."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from hushclaw.app_connectors import x as x_connector
from hushclaw.config.schema import InboundAutomationConfig, InboundAutomationRuleConfig
from hushclaw.tools.base import ToolResult
from hushclaw.util.logging import get_logger

log = get_logger("app_connectors.inbound")

_AUTO_REPLY_ACTIONS = {"auto_reply"}
_QUEUE_ACTIONS = {"queue_only", "draft_only"}
_REPLY_STATUSES = {"auto_replied", "replied", "published"}
_DEFAULT_PROMPT = (
    "Write a single public reply for an inbound social message.\n"
    "Constraints:\n"
    "- Output only the reply text.\n"
    "- Keep the same language as the inbound message unless a different language is clearly required.\n"
    "- Be concise, natural, and specific.\n"
    "- Do not use markdown fences, bullet lists, or surrounding quotation marks.\n"
    "- Stay within {max_reply_chars} characters.\n"
)


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class InboundEvent:
    event_id: str
    connector_id: str
    record_event_type: str
    external_id: str
    title: str
    body: str
    source_url: str
    status: str
    created: int
    updated: int
    payload: dict
    normalized_event_type: str
    thread_id: str
    author_external_id: str
    author_username: str
    target_external_id: str
    matched_rule_tags: list[str]

    @classmethod
    def from_record(cls, record: dict) -> "InboundEvent":
        payload = dict(record.get("payload") or {})
        normalized_event_type = str(
            payload.get("normalized_event_type")
            or payload.get("event_type")
            or record.get("event_type")
            or "message"
        ).strip() or "message"
        matched_rule_tags = payload.get("matched_rule_tags")
        if not isinstance(matched_rule_tags, list):
            matched_rule_tags = []
        return cls(
            event_id=str(record.get("event_id") or ""),
            connector_id=str(record.get("connector_id") or ""),
            record_event_type=str(record.get("event_type") or ""),
            external_id=str(record.get("external_id") or ""),
            title=str(record.get("title") or ""),
            body=str(record.get("body") or ""),
            source_url=str(record.get("source_url") or ""),
            status=str(record.get("status") or ""),
            created=int(record.get("created") or 0),
            updated=int(record.get("updated") or 0),
            payload=payload,
            normalized_event_type=normalized_event_type,
            thread_id=str(payload.get("thread_id") or payload.get("conversation_id") or record.get("external_id") or ""),
            author_external_id=str(payload.get("author_external_id") or ""),
            author_username=str(payload.get("author_username") or ""),
            target_external_id=str(payload.get("target_external_id") or ""),
            matched_rule_tags=[str(tag).strip() for tag in matched_rule_tags if str(tag).strip()],
        )


@dataclass
class InboundDecision:
    action: str
    matched_rule: str = ""
    matched_rule_tags: list[str] | None = None
    agent: str = "default"
    reason: str = ""
    cooldown_key: str = ""
    prompt_template: str = ""


class InboundStrategyEngine:
    def __init__(self, config: InboundAutomationConfig) -> None:
        self.config = config

    def decide(self, event: InboundEvent, connector_config, recent_events: list[dict]) -> InboundDecision:
        matching_rule = self._find_matching_rule(event)
        action = self.config.default_action
        if matching_rule is not None:
            action = str(matching_rule.action or "auto_reply").strip() or "auto_reply"
        decision = InboundDecision(
            action=action,
            matched_rule=str(matching_rule.name or "") if matching_rule is not None else "",
            matched_rule_tags=list(event.matched_rule_tags),
            agent=str(
                (matching_rule.agent if matching_rule is not None else "")
                or self.config.default_agent
                or "default"
            ).strip() or "default",
            reason="rule_match" if matching_rule is not None else "default_action",
            cooldown_key=self._cooldown_key(event, matching_rule.cooldown_scope if matching_rule is not None else "thread_author"),
            prompt_template=str(matching_rule.prompt_template or "") if matching_rule is not None else "",
        )
        if action in _AUTO_REPLY_ACTIONS and matching_rule is None:
            decision.action = "queue_only"
            decision.reason = "auto_reply_requires_rule"
            return decision
        if action in _AUTO_REPLY_ACTIONS and getattr(connector_config, "allow_actions", False) is False:
            decision.action = "queue_only"
            decision.reason = "allow_actions_disabled"
            return decision
        if action in _AUTO_REPLY_ACTIONS and matching_rule is not None and matching_rule.require_allow_actions and not getattr(connector_config, "allow_actions", False):
            decision.action = "queue_only"
            decision.reason = "allow_actions_required"
            return decision
        if action in _AUTO_REPLY_ACTIONS and matching_rule is not None and matching_rule.cooldown_seconds > 0:
            cutoff = int(time.time()) - int(matching_rule.cooldown_seconds)
            for item in recent_events:
                if str(item.get("event_id") or "") == event.event_id:
                    continue
                if str(item.get("status") or "") not in _REPLY_STATUSES:
                    continue
                payload = item.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("cooldown_key") or "") != decision.cooldown_key:
                    continue
                if int(item.get("updated") or 0) >= cutoff:
                    decision.action = "queue_only"
                    decision.reason = "cooldown_active"
                    return decision
        return decision

    def _find_matching_rule(self, event: InboundEvent) -> InboundAutomationRuleConfig | None:
        for rule in self.config.rules:
            if not rule.enabled:
                continue
            if rule.connector_id and rule.connector_id != event.connector_id:
                continue
            if rule.event_types and event.normalized_event_type not in set(rule.event_types):
                continue
            if rule.rule_tags and not set(rule.rule_tags).intersection(event.matched_rule_tags):
                continue
            if rule.author_allowlist and event.author_external_id not in set(rule.author_allowlist):
                continue
            if rule.author_denylist and event.author_external_id in set(rule.author_denylist):
                return InboundAutomationRuleConfig(name=rule.name, action="ignore")
            if rule.thread_ids and event.thread_id not in set(rule.thread_ids):
                continue
            return rule
        return None

    @staticmethod
    def _cooldown_key(event: InboundEvent, scope: str) -> str:
        scope = str(scope or "thread_author").strip() or "thread_author"
        if scope == "author":
            return f"{event.connector_id}:author:{event.author_external_id or 'unknown'}"
        if scope == "thread":
            return f"{event.connector_id}:thread:{event.thread_id or event.external_id or 'unknown'}"
        if scope == "global":
            return f"{event.connector_id}:global"
        return (
            f"{event.connector_id}:thread_author:"
            f"{event.thread_id or event.external_id or 'unknown'}:{event.author_external_id or 'unknown'}"
        )


class InboundAutomationWorker:
    """Poll unread inbound events and apply auto-reply rules."""

    def __init__(self, config, gateway, memory_store, secrets) -> None:
        self.config = config
        self.gateway = gateway
        self.memory_store = memory_store
        self.secrets = secrets
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def should_start(self) -> bool:
        auto_cfg = getattr(self.config, "inbound_automation", None)
        return bool(
            auto_cfg is not None
            and getattr(auto_cfg, "enabled", False)
            and self.gateway is not None
            and self.memory_store is not None
        )

    async def start(self) -> None:
        if not self.should_start():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="app-inbound-automation")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = max(1, int(getattr(self.config.inbound_automation, "poll_interval_seconds", 15) or 15))
        while not self._stopping.is_set():
            try:
                processed = await self._process_batch()
                await asyncio.sleep(0.25 if processed else backoff)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Inbound automation loop failed: %s", exc, exc_info=True)
                await asyncio.sleep(backoff)

    async def _process_batch(self) -> int:
        auto_cfg = self.config.inbound_automation
        engine = InboundStrategyEngine(auto_cfg)
        unread = self.memory_store.list_app_inbox_events(status="unread", limit=max(1, int(auto_cfg.batch_size or 10)))
        processed = 0
        for record in unread:
            event = InboundEvent.from_record(record)
            connector_cfg = getattr(self.config, event.connector_id, None)
            if connector_cfg is None:
                self.memory_store.patch_app_inbox_event(
                    event.event_id,
                    status="classified",
                    payload_patch={"policy_decision": "queue_only", "policy_reason": "unsupported_connector"},
                )
                processed += 1
                continue
            recent = self.memory_store.list_app_inbox_events(connector_id=event.connector_id, limit=200)
            decision = engine.decide(event, connector_cfg, recent)
            patch = {
                "policy_decision": decision.action,
                "policy_reason": decision.reason,
                "policy_rule": decision.matched_rule,
                "cooldown_key": decision.cooldown_key,
                "decision_agent": decision.agent,
                "automation_checked_at": int(time.time()),
            }
            if decision.action == "ignore":
                self.memory_store.patch_app_inbox_event(event.event_id, status="ignored", payload_patch=patch)
                processed += 1
                continue
            if decision.action in _QUEUE_ACTIONS or decision.action not in _AUTO_REPLY_ACTIONS:
                self.memory_store.patch_app_inbox_event(event.event_id, status="classified", payload_patch=patch)
                processed += 1
                continue
            claimed = self.memory_store.claim_app_inbox_event(
                event.event_id,
                from_statuses=["unread", "classified"],
                to_status="pending",
                payload_patch=patch,
            )
            if claimed is None:
                continue
            await self._execute_auto_reply(InboundEvent.from_record(claimed), decision)
            processed += 1
        return processed

    async def _execute_auto_reply(self, event: InboundEvent, decision: InboundDecision) -> None:
        try:
            reply_text = await self._generate_reply(event, decision)
            publish = self._send_reply(event, reply_text)
            if isinstance(publish, ToolResult):
                if publish.is_error:
                    raise RuntimeError(publish.content)
                payload = self._parse_json(publish.content)
            else:
                payload = publish
            self.memory_store.patch_app_inbox_event(
                event.event_id,
                status="auto_replied",
                payload_patch={
                    "reply_text": reply_text,
                    "reply_result": payload,
                    "reply_sent_at": int(time.time()),
                    "last_error": "",
                },
            )
        except Exception as exc:
            self.memory_store.patch_app_inbox_event(
                event.event_id,
                status="failed",
                payload_patch={
                    "last_error": str(exc),
                    "failed_at": int(time.time()),
                },
            )
            log.warning("Inbound auto-reply failed event=%s: %s", event.event_id, exc, exc_info=True)

    async def _generate_reply(self, event: InboundEvent, decision: InboundDecision) -> str:
        auto_cfg = self.config.inbound_automation
        prompt = self._build_prompt(event, decision, max_reply_chars=max(32, int(auto_cfg.max_reply_chars or 280)))
        session_id = f"app-inbound:{event.connector_id}:{event.thread_id or event.external_id or event.event_id}"
        raw = await self.gateway.execute(decision.agent, prompt, session_id=session_id)
        text = self._sanitize_reply_text(raw, max_chars=max(32, int(auto_cfg.max_reply_chars or 280)))
        if not text:
            raise RuntimeError("generated empty auto-reply")
        return text

    def _build_prompt(self, event: InboundEvent, decision: InboundDecision, *, max_reply_chars: int) -> str:
        context = {
            "connector_id": event.connector_id,
            "event_type": event.normalized_event_type,
            "title": event.title,
            "body": event.body,
            "source_url": event.source_url,
            "thread_id": event.thread_id,
            "author_external_id": event.author_external_id,
            "author_username": event.author_username,
            "target_external_id": event.target_external_id,
            "matched_rule_tags": ", ".join(event.matched_rule_tags),
            "max_reply_chars": str(max_reply_chars),
        }
        prompt = _DEFAULT_PROMPT.format(max_reply_chars=max_reply_chars)
        if decision.prompt_template:
            prompt += "\nRule instructions:\n"
            prompt += decision.prompt_template.format_map(_SafeFormatDict(context)).strip()
            prompt += "\n"
        prompt += (
            "\nInbound context:\n"
            f"- Connector: {event.connector_id}\n"
            f"- Event type: {event.normalized_event_type}\n"
            f"- Author id: {event.author_external_id or '(unknown)'}\n"
            f"- Author username: {event.author_username or '(unknown)'}\n"
            f"- Thread id: {event.thread_id or '(unknown)'}\n"
            f"- Source URL: {event.source_url or '(none)'}\n"
            f"- Matched rule tags: {', '.join(event.matched_rule_tags) or '(none)'}\n"
            "\nInbound message:\n"
            f"{event.body or event.title}\n"
        )
        return prompt

    def _send_reply(self, event: InboundEvent, text: str):
        connector_cfg = getattr(self.config, event.connector_id, None)
        if event.connector_id == "x":
            return x_connector.reply(
                connector_cfg,
                self.secrets,
                post_id=event.external_id,
                text=text,
                memory_store=self.memory_store,
            )
        raise RuntimeError(f"unsupported inbound reply connector: {event.connector_id}")

    @staticmethod
    def _sanitize_reply_text(raw: str, *, max_chars: int) -> str:
        text = str(raw or "").strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if "\n" in text:
                    text = text.split("\n", 1)[1]
        text = text.strip().strip('"').strip("'").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text

    @staticmethod
    def _parse_json(raw: str) -> dict | str:
        try:
            return json.loads(raw)
        except Exception:
            return str(raw)
