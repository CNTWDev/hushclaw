from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hushclaw.runtime.services import RuntimeServices


def _make_config(*, auto_extract: bool = True, workspace_dir: Path | None = None, timezone: str = "Asia/Shanghai"):
    return SimpleNamespace(
        context=SimpleNamespace(auto_extract=auto_extract),
        agent=SimpleNamespace(workspace_dir=workspace_dir),
        calendar=SimpleNamespace(timezone=timezone),
    )


def test_runtime_services_resolve_context_engine_uses_latest_config(tmp_path: Path):
    services = RuntimeServices(MagicMock(), _make_config(timezone="Asia/Shanghai"))
    services.set_config(_make_config(workspace_dir=tmp_path, timezone="America/Los_Angeles"))

    engine = services._resolve_context_engine()

    assert engine.auto_extract is True
    assert engine._workspace_dir == tmp_path
    assert engine._calendar_timezone == "America/Los_Angeles"


def test_runtime_services_ensure_started_reuses_existing_workers():
    memory = MagicMock()
    config = _make_config()
    projection_instances: list[object] = []
    retention_instances: list[object] = []

    class _FakeProjectionWorker:
        def __init__(self, _memory, _engine):
            self._task = None
            projection_instances.append(self)

        def start(self):
            self._task = SimpleNamespace(done=lambda: False)

        async def stop(self):
            self._task = None

    class _FakeRetentionExecutor:
        def __init__(self, _memory):
            self._task = None
            retention_instances.append(self)

        def start(self):
            self._task = SimpleNamespace(done=lambda: False)

        async def stop(self):
            self._task = None

    with (
        patch("hushclaw.runtime.services.ProjectionWorker", _FakeProjectionWorker),
        patch("hushclaw.runtime.services.RetentionExecutor", _FakeRetentionExecutor),
    ):
        services = RuntimeServices(memory, config)
        services.ensure_started()
        first_projection = services.projection_worker
        first_retention = services.retention_executor

        services.ensure_started()

    assert services.projection_worker is first_projection
    assert services.retention_executor is first_retention
    assert len(projection_instances) == 1
    assert len(retention_instances) == 1
