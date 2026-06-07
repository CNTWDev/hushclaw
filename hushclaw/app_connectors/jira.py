"""Jira Cloud App Connector."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.util.ssl_context import make_ssl_context


class JiraAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="jira",
        name="Jira",
        description="Connect Jira Cloud issues, projects, and search.",
        capabilities=["issues.read", "projects.read", "search"],
        auth="Atlassian OAuth 2.0 or Jira API token",
        sdk="atlassian-python-api / Jira Cloud REST API",
        docs_url="https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
    )

    def configured(self) -> bool:
        token_ref = getattr(self.config, "token_ref", "app_connectors.jira.token")
        access_ref = getattr(self.config, "access_token_ref", "app_connectors.jira.access_token")
        return bool(getattr(self.config, "site_url", "") and (self.secrets.get(token_ref) or self.secrets.get(access_ref)))


def test_jira_connection(config, secrets) -> dict:
    site_url = str(getattr(config, "site_url", "") or "").strip().rstrip("/")
    email = str(getattr(config, "email", "") or "").strip()
    token_ref = getattr(config, "token_ref", "app_connectors.jira.token")
    access_ref = getattr(config, "access_token_ref", "app_connectors.jira.access_token")
    token = secrets.get(token_ref)
    access_token = secrets.get(access_ref)
    if not site_url:
        return {"ok": False, "message": "Jira site URL is not configured."}
    headers = {"Accept": "application/json", "User-Agent": "HushClaw-AppConnector/1.0"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif email and token:
        raw = f"{email}:{token}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    else:
        return {"ok": False, "message": "Jira API token or OAuth access token is not configured."}
    req = urllib.request.Request(f"{site_url}/rest/api/3/myself", method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=make_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"errorMessages": [raw]}
        msg = payload.get("message")
        if not msg and isinstance(payload.get("errorMessages"), list):
            msg = "; ".join(payload["errorMessages"])
        return {"ok": False, "message": msg or "Jira token check failed."}
    return {"ok": True, "message": f"Connected as {payload.get('displayName') or payload.get('emailAddress') or 'Jira user'}."}
