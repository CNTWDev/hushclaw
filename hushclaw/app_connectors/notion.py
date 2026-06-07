"""Notion App Connector."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.util.ssl_context import make_ssl_context

API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"


class NotionAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="notion",
        name="Notion",
        description="Connect Notion pages, databases, and workspace search.",
        capabilities=["pages.read", "databases.read", "search"],
        auth="Internal integration token or OAuth",
        sdk="notion-client",
        docs_url="https://developers.notion.com/docs/getting-started",
    )

    def configured(self) -> bool:
        token_ref = getattr(self.config, "token_ref", "app_connectors.notion.token")
        return bool(self.secrets.get(token_ref))


def test_notion_connection(config, secrets) -> dict:
    token_ref = getattr(config, "token_ref", "app_connectors.notion.token")
    token = secrets.get(token_ref)
    if not token:
        return {"ok": False, "message": "Notion token is not configured."}
    req = urllib.request.Request(
        f"{API}/users/me",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Accept": "application/json",
            "User-Agent": "HushClaw-AppConnector/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=make_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"message": raw}
        return {"ok": False, "message": payload.get("message") or "Notion token check failed."}
    name = payload.get("name") or payload.get("bot", {}).get("owner", {}).get("workspace", True)
    return {"ok": True, "message": f"Connected to Notion ({name})."}
