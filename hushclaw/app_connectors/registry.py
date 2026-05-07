"""Registry for enabled outbound app connector tools."""
from __future__ import annotations

from hushclaw.app_connectors.github import GitHubAppConnector
from hushclaw.app_connectors.google_workspace import GoogleWorkspaceAppConnector
from hushclaw.app_connectors.jira import JiraAppConnector
from hushclaw.app_connectors.notion import NotionAppConnector
from hushclaw.secrets import get_secret_store


class AppConnectorRegistry:
    def __init__(self, config, secrets=None) -> None:
        self.config = config
        self.secrets = secrets or get_secret_store()
        self._connectors = {
            "github": GitHubAppConnector(config.github, self.secrets),
            "google_workspace": GoogleWorkspaceAppConnector(config.google_workspace, self.secrets),
            "notion": NotionAppConnector(config.notion, self.secrets),
            "jira": JiraAppConnector(config.jira, self.secrets),
        }

    def enabled_tools(self):
        tools = []
        for connector in self._connectors.values():
            if connector.enabled() and connector.configured():
                tools.extend(connector.tools())
        return tools

    def status(self) -> dict:
        return {
            key: connector.status()
            for key, connector in self._connectors.items()
        }
