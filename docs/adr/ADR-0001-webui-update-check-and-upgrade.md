# ADR-0001: WebUI Update Check and Upgrade

## Status
Accepted

## Context
The WebUI needs a first-class update experience:
- Users should manually check for updates from UI.
- Users should optionally enable auto-check with interval control.
- Upgrade execution must require explicit user confirmation.
- Update logic must be isolated from chat/server core behavior.

## Decision
Implement a dedicated update subsystem with three backend components:
- `hushclaw/update/provider.py`: release metadata source strategy (`GithubReleaseProvider`).
- `hushclaw/update/service.py`: version comparison, cache, and policy-aware check orchestration.
- `hushclaw/update/executor.py`: upgrade command execution with lock + streamed progress.

Expose update actions over WebSocket:
- Client -> Server: `check_update`, `save_update_policy`, `run_update`
- Server -> Client: `update_status`, `update_available`, `update_progress`, `update_result`

Persist update policy in config under `[update]`:
- `auto_check_enabled`
- `check_interval_hours`
- `channel`
- `last_checked_at`
- `check_timeout_seconds`
- `cache_ttl_seconds`
- `upgrade_timeout_seconds`

Front-end integration:
- Add an `Updates` section in System settings.
- Auto-check on config load when policy interval is due.
- Use a confirmation modal (`window.confirm`) before `run_update`.

## Alternatives Considered
1. Put update logic directly inside `server.py`:
   - Rejected due to high coupling and low testability.
2. Auto-install without confirmation:
   - Rejected due to operational/safety risk.
3. Poll npm/pip registries instead of GitHub release:
   - Rejected for this iteration because repository releases are source-of-truth.

## Consequences
Positive:
- Clear separation of concerns and easier testing.
- Upgrade behavior is observable via progress events.
- Safe default (check-only, user confirms install).

Trade-offs:
- More protocol surface area in WebSocket handlers.
- Upgrade command behavior still depends on host environment and install mode.

## Rollout Plan
1. Ship check-only (`check_update` + UI status).
2. Enable confirmation + `run_update` with progress stream.
3. Monitor failures and adjust fallback command selection per platform.
