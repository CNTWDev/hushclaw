"""Background runtime services owned by a harness runtime."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.context.engine import ContextEngine, DefaultContextEngine
from hushclaw.memory.projection import ProjectionWorker
from hushclaw.runtime.retention import RetentionExecutor

if TYPE_CHECKING:
    from hushclaw.config import Config
    from hushclaw.memory.store import MemoryStore


class RuntimeServices:
    """Own background runtime services such as projections and retention.

    This keeps process-level/background concerns out of ``AgentLoop`` and lets
    ``Agent`` stay focused on composition and loop creation.
    """

    def __init__(
        self,
        memory: "MemoryStore",
        config: "Config",
        *,
        context_engine: ContextEngine | None = None,
        projection_worker: ProjectionWorker | None = None,
        retention_executor: RetentionExecutor | None = None,
    ) -> None:
        self._memory = memory
        self._config = config
        self._context_engine = context_engine
        self._projection_worker = projection_worker
        self._retention_executor = retention_executor

    @property
    def projection_worker(self) -> ProjectionWorker | None:
        return self._projection_worker

    @property
    def retention_executor(self) -> RetentionExecutor | None:
        return self._retention_executor

    def set_context_engine(self, context_engine: ContextEngine | None) -> None:
        self._context_engine = context_engine

    def set_config(self, config: "Config") -> None:
        self._config = config

    def ensure_started(self, context_engine: ContextEngine | None = None) -> None:
        if context_engine is not None:
            self._context_engine = context_engine
        self._ensure_projection_worker()
        self._ensure_retention_executor()

    async def stop(self) -> None:
        for worker in (self._projection_worker, self._retention_executor):
            if worker is None:
                continue
            await worker.stop()

    def _ensure_projection_worker(self) -> None:
        worker = self._projection_worker
        if worker is not None and not (worker._task is None or worker._task.done()):
            return
        self._projection_worker = ProjectionWorker(self._memory, self._resolve_context_engine())
        self._projection_worker.start()

    def _ensure_retention_executor(self) -> None:
        if self._retention_executor is None:
            self._retention_executor = RetentionExecutor(self._memory)
        self._retention_executor.start()

    def _resolve_context_engine(self) -> ContextEngine:
        if self._context_engine is not None:
            return self._context_engine
        return DefaultContextEngine(
            auto_extract=self._config.context.auto_extract,
            workspace_dir=self._config.agent.workspace_dir,
            calendar_timezone=getattr(self._config.calendar, "timezone", ""),
        )
