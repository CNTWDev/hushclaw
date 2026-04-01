# ADR-0002: X Operator Skill Task Contract and Runtime Safety

Date: 2026-04-01

## Status

Accepted

## Context

HushClaw scheduled tasks are persisted and executed as `cron + prompt + agent`, then dispatched through `gateway.execute(...)`.  
We need a full-automation X skill that:

- fits this scheduler model without creating a second scheduler
- remains idempotent under retries or overlap runs
- classifies external API failures for task-layer observability

## Decision

1. Use a fixed trigger phrase (`执行 X 运营一轮 tick`) as the deterministic task contract.
2. Expose a single bounded round executor tool: `x_operator_tick(...)`.
3. Keep scheduling in system layer; keep strategy + API execution in skill layer.
4. Enforce runtime guards in skill: kill switch, mode, backoff, per-tick and daily write caps.
5. Use official X API only with explicit retryable/fatal error classification.

## SOLID application

- Single Responsibility: adapter handles HTTP/auth; tick orchestrates one round; profile tools handle config.
- Open/Closed: new action types can be added via new action handlers without changing scheduler contract.
- Liskov Substitution: action handlers return the same result envelope shape.
- Interface Segregation: small focused tools (`save_profile`, `set_mode`, `status`, `tick`).
- Dependency Inversion: tick depends on adapter helper contract, not on transport details from scheduler.

## Options considered

| Option | Pros | Cons | Decision |
|---|---|---|---|
| Official X API only | Stable contract, clearer policy boundaries | Requires proper token setup | Accepted |
| Unofficial scraping | Faster prototyping | High policy and breakage risk | Rejected |
| Separate daemon scheduler | Flexible | Conflicts with existing system scheduler | Rejected |

## Consequences

- Positive: compatible with current task architecture and easier to operate.
- Positive: failures become actionable via retryable/fatal split.
- Tradeoff: deterministic behavior depends on preserving the fixed trigger phrase.

## Exception handling strategy

- `401/403` -> fatal, switch mode to `quiet`.
- `429/5xx` -> retryable, set backoff window.
- validation failures -> fatal for action, keep run bounded.
- lock contention -> skip with `already_running`.
