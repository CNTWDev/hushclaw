"""Google password authentication fails locally with actionable guidance."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hushclaw.config.schema import CalendarConfig
from hushclaw.server.integration_handler import handle_test_calendar
from hushclaw.util.caldav_auth import validate_caldav_password_auth


@pytest.mark.parametrize("url", [
    "https://www.google.com/calendar/dav",
    "https://www.google.com/calendar/dav/user%40gmail.com/events/",
    "https://calendar.google.com/calendar/dav/user/events",
    " www.google.com/calendar/dav ",
    "https://WWW.GOOGLE.COM./calendar/dav/",
    "https://apidata.googleusercontent.com/caldav/v2/user%40gmail.com/user",
    "https://apidata.googleusercontent.com/caldav/v2/user/events",
])
def test_google_requires_oauth(url):
    with pytest.raises(ValueError, match="requires OAuth 2.0"):
        validate_caldav_password_auth(url)


@pytest.mark.parametrize("url", [
    "https://caldav.icloud.com",
    "https://caldav.fastmail.com",
    "https://nextcloud.example/remote.php/dav",
    "https://www.google.com.example/calendar/dav",
    "https://example.com/calendar/dav?host=www.google.com",
])
def test_other_providers_are_not_rejected(url):
    validate_caldav_password_auth(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_saved_config", [False, True])
async def test_connection_test_rejects_google_without_network(use_saved_config):
    calendar = CalendarConfig(
        url="https://www.google.com/calendar/dav",
        username="user@gmail.com", password="secret-password",
    )
    gateway = SimpleNamespace(base_agent=SimpleNamespace(config=SimpleNamespace(calendar=calendar)))
    ws = SimpleNamespace(send=AsyncMock())
    caldav = MagicMock()
    data = {} if use_saved_config else vars(calendar)
    with patch.dict("sys.modules", {"caldav": caldav}):
        await handle_test_calendar(ws, data, gateway)
    caldav.DAVClient.assert_not_called()
    messages = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert messages[-1]["type"] == "test_integration_result"
    assert messages[-1]["ok"] is False
    assert "OAuth 2.0" in messages[-1]["message"]
    assert "secret-password" not in json.dumps(messages)


@pytest.mark.asyncio
async def test_other_provider_can_still_connect():
    calendar = CalendarConfig(url="https://caldav.icloud.com", username="user", password="app-password")
    gateway = SimpleNamespace(base_agent=SimpleNamespace(config=SimpleNamespace(calendar=calendar)))
    ws = SimpleNamespace(send=AsyncMock())
    caldav = MagicMock()
    caldav.DAVClient.return_value.principal.return_value.calendars.return_value = [SimpleNamespace(name="Personal")]
    with patch.dict("sys.modules", {"caldav": caldav}):
        await handle_test_calendar(ws, {}, gateway)
    caldav.DAVClient.assert_called_once_with(url=calendar.url, username="user", password="app-password")
    assert json.loads(ws.send.call_args.args[0])["ok"] is True


def test_background_sync_rejects_google_before_connecting():
    from hushclaw.connectors.caldav_sync import CalDAVSyncService

    calendar = CalendarConfig(url="https://www.google.com/calendar/dav", username="user")
    service = CalDAVSyncService(calendar, MagicMock())
    caldav = MagicMock()
    with patch.dict("sys.modules", {"caldav": caldav}):
        with pytest.raises(ValueError, match="requires OAuth 2.0"):
            service._do_sync(calendar)
    caldav.DAVClient.assert_not_called()


def test_calendar_tools_reject_google_before_connecting():
    from hushclaw.tools.builtins import calendar_tools

    calendar = CalendarConfig(url="https://www.google.com/calendar/dav", username="user")
    with patch.object(calendar_tools, "caldav", create=True) as caldav:
        with pytest.raises(ValueError, match="requires OAuth 2.0"):
            calendar_tools._caldav_client(calendar)
    caldav.DAVClient.assert_not_called()
