"""Read-only Google Calendar API client using the Workspace OAuth credentials."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.oauth import OAuthError, _form_request
from hushclaw.util.ssl_context import make_ssl_context

API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarClient:
    def __init__(self, config, secrets, stop_event=None):
        self.config = config
        self.secrets = secrets
        self.stop_event = stop_event
        self._access_token = secrets.get(config.access_token_ref)

    def _refresh(self):
        cfg = self.config
        refresh = self.secrets.get(cfg.refresh_token_ref)
        client_id = self.secrets.get(cfg.client_id_ref)
        client_secret = self.secrets.get(cfg.client_secret_ref)
        if not refresh:
            raise OAuthError("Connect Google Workspace again to authorize Calendar access.")
        if not client_id or not client_secret:
            raise OAuthError(
                "Google authorization has expired. Reconnect Google Workspace, or configure "
                "a Custom OAuth app with its Client ID and Client secret for automatic token refresh."
            )
        try:
            payload = _form_request("https://oauth2.googleapis.com/token", data={
                "grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": client_id, "client_secret": client_secret,
            })
        except OAuthError:
            raise OAuthError("Google token refresh failed. Reconnect Google Workspace using the same OAuth app.") from None
        token = payload.get("access_token")
        if not token:
            raise OAuthError("Google did not return an access token. Reconnect Google Workspace.")
        self._access_token = token
        self.secrets.set(cfg.access_token_ref, token)
        if payload.get("refresh_token"):
            self.secrets.set(cfg.refresh_token_ref, payload["refresh_token"])

    def _get(self, path, params):
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("Google Calendar sync stopped.")
        if not self._access_token:
            self._refresh()
        url = API_BASE + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(2):
            request = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + self._access_token,
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=20, context=make_ssl_context()) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError("Google Calendar returned an invalid response.")
                return payload
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if status == 401 and attempt == 0:
                    self._refresh()
                    continue
                if status == 401:
                    raise OAuthError("Google authorization was rejected. Reconnect Google Workspace.") from None
                if status == 403:
                    raise OAuthError(
                        "Google Calendar access was denied. Enable Google Calendar API in your "
                        "Google Cloud project and reconnect with calendar.readonly permission. "
                        "Also check the project's API quota."
                    ) from None
                raise RuntimeError(f"Google Calendar request failed (HTTP {status}). Try syncing again later.") from None

    def _items(self, path, params):
        params = dict(params)
        seen_pages = set()
        while True:
            payload = self._get(path, params)
            items = payload.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Google Calendar returned an invalid item list.")
            yield from items
            token = payload.get("nextPageToken")
            if not token:
                return
            if token in seen_pages:
                raise ValueError("Google Calendar returned a repeated page token.")
            seen_pages.add(token)
            params["pageToken"] = token

    def calendars(self):
        return list(self._items("/users/me/calendarList", {"minAccessRole": "reader", "maxResults": 250}))

    def events(self, calendar_id, start, end):
        path = "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events"
        return self._items(path, {
            "timeMin": start, "timeMax": end, "singleEvents": "true",
            "showDeleted": "false", "maxResults": 2500, "timeZone": "UTC",
        })
