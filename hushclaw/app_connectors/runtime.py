"""Runtime services for App Connectors."""
from __future__ import annotations

from hushclaw.secrets import get_secret_store
from hushclaw.util.logging import get_logger

log = get_logger("app_connectors.runtime")


class AppConnectorRuntimeManager:
    """Owns background App Connector listeners.

    Gateway/channel connectors live in ``hushclaw.connectors``. This manager is
    intentionally separate so platform-specific App Connectors can add runtimes
    without becoming chat gateways.
    """

    def __init__(self, config, memory_store, gateway=None, secrets=None) -> None:
        self.config = config
        self.memory_store = memory_store
        self.gateway = gateway
        self.secrets = secrets or get_secret_store()
        self._services: list[object] = []

    async def start(self) -> None:
        await self.stop()
        self._services = []
        x_cfg = getattr(self.config, "x", None)
        if x_cfg is not None:
            from hushclaw.app_connectors.x_stream import XFilteredStreamWorker

            worker = XFilteredStreamWorker(x_cfg, self.secrets, self.memory_store)
            if worker.should_start():
                self._services.append(worker)
        from hushclaw.app_connectors.inbound import InboundAutomationWorker

        worker = InboundAutomationWorker(self.config, self.gateway, self.memory_store, self.secrets)
        if worker.should_start():
            self._services.append(worker)
        for service in self._services:
            try:
                await service.start()
            except Exception as exc:
                log.error("Failed to start App Connector runtime %s: %s", service.__class__.__name__, exc)

    async def stop(self) -> None:
        for service in list(self._services):
            try:
                await service.stop()
            except Exception as exc:
                log.warning("Failed to stop App Connector runtime %s: %s", service.__class__.__name__, exc)
        self._services = []

    async def reload(self, config, memory_store=None, gateway=None) -> None:
        self.config = config
        if memory_store is not None:
            self.memory_store = memory_store
        if gateway is not None:
            self.gateway = gateway
        await self.start()
