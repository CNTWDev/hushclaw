# hushclaw-skill-x-operator

Official X API based personal-account automation skill for HushClaw scheduled tasks.

## What this skill solves

- deterministic task entry via fixed phrase trigger
- single-round idempotent execution (`x_operator_tick`)
- defensive runtime controls (kill switch, mode, backoff, write caps)
- retryable/fatal API classification for task-level observability

## Task trigger contract

Use this exact phrase inside scheduled task prompt:

`执行 X 运营一轮 tick`

Example scheduled prompt:

`执行 X 运营一轮 tick：处理 mention 并按配置回复，最多 3 条写操作。`

## Setup

1. Set official X user access token (OAuth2 user context):
   - `export X_OPERATOR_ACCESS_TOKEN="..."`
2. Optional global kill switch:
   - `export X_AUTOMATION_ENABLED=true`
3. Initialize profile:
   - call `x_operator_save_profile(...)`
4. Enable mode:
   - call `x_operator_set_mode("normal")`

## Tools

- `x_operator_validate_task_prompt(task_prompt)`
- `x_operator_save_profile(...)`
- `x_operator_set_mode(mode)`
- `x_operator_status()`
- `x_operator_tick(task_prompt, dry_run, max_writes, mention_batch)`

## Decision matrix

| Option | Stability | Policy Risk | Operational Cost | Decision |
|---|---|---:|---:|---|
| Official X API only | High | Lower | Medium | Selected |
| Mixed official + unofficial scraping | Medium | High | Medium | Rejected |
| Browser automation only | Low | High | High | Rejected |

## Exception handling strategy

- `401/403`: fatal, switch mode to `quiet`, require operator intervention.
- `429`: retryable, set `backoff_until` and skip current run.
- `5xx`: retryable, set `backoff_until`.
- malformed payload: fatal for current action, continue other actions where safe.

## Suggested scheduler settings

- run every 5-15 minutes
- prefer `gateway.scheduled_session_mode = "run"`
- keep each tick bounded: `max_writes <= 3` by default
