# X Operator Test Matrix

## Scope

This matrix validates the `hushclaw-skill-x-operator` contract under scheduled-task execution.

## Matrix

| Area | Case | Expected |
|---|---|---|
| Trigger contract | Prompt contains `执行 X 运营一轮 tick` | Tick route is eligible |
| Trigger contract | Prompt does not contain phrase | Tool rejects contract |
| Idempotency | Same action key replayed | Duplicate is skipped |
| Locking | Tick starts while another run lock is active | Second run is skipped as `already_running` |
| Error mapping | 401/403 from X API | Fatal classification + mode fallback |
| Error mapping | 429/5xx from X API | Retryable classification + backoff |
| Session strategy | `scheduled_session_mode=run` | Each run gets isolated scheduler session |
| Dry run | `dry_run=true` | Decision path runs, no external writes |

## Automated coverage

- `tests/test_x_operator_skill.py`
  - prompt contract
  - HTTP classification
  - idempotency key stability
  - lock acquire/release behavior
- `tests/test_scheduler.py`
  - cron semantics (`0=Monday`)
  - scheduled session mode behavior (`job`/`run`)
