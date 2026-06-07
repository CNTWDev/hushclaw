"""Google Workspace App Connector.

The production integration path is OAuth 2.0 with google-api-python-client.
This module keeps v1 dependency-light: it stores OAuth credentials and can
validate an access token against Google's tokeninfo endpoint.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.util.ssl_context import make_ssl_context


class GoogleWorkspaceAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="google_workspace",
        name="Google Workspace",
        description="Connect Google Drive, Gmail, Calendar, and Docs through Google OAuth.",
        capabilities=["drive.read", "gmail.read", "calendar.read", "docs.read"],
        auth="OAuth 2.0",
        sdk="google-api-python-client / google-auth",
        docs_url="https://developers.google.com/workspace/guides/auth-overview",
    )

    def configured(self) -> bool:
        access_ref = getattr(self.config, "access_token_ref", "app_connectors.google_workspace.access_token")
        refresh_ref = getattr(self.config, "refresh_token_ref", "app_connectors.google_workspace.refresh_token")
        return bool(self.secrets.get(access_ref) or self.secrets.get(refresh_ref))


def test_google_workspace_connection(config, secrets) -> dict:
    access_ref = getattr(config, "access_token_ref", "app_connectors.google_workspace.access_token")
    refresh_ref = getattr(config, "refresh_token_ref", "app_connectors.google_workspace.refresh_token")
    access_token = secrets.get(access_ref)
    refresh_token = secrets.get(refresh_ref)
    if not access_token and refresh_token:
        return {
            "ok": True,
            "message": "Refresh token is stored. Access token refresh will be handled by the Google SDK adapter.",
        }
    if not access_token:
        return {"ok": False, "message": "Google OAuth access token or refresh token is not configured."}

    query = urllib.parse.urlencode({"access_token": access_token})
    req = urllib.request.Request(f"https://oauth2.googleapis.com/tokeninfo?{query}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15, context=make_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error_description": raw}
        return {"ok": False, "message": payload.get("error_description") or payload.get("error") or "Token check failed."}

    email = payload.get("email", "Google account")
    return {"ok": True, "message": f"Connected to {email}."}
