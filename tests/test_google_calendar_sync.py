"""OAuth refresh, Google pagination, and safe Calendar snapshot integration."""
import io
import json
import time
import urllib.error
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from hushclaw.app_connectors.google_calendar import GoogleCalendarClient
from hushclaw.app_connectors.oauth import OAuthError
from hushclaw.config.schema import CalendarConfig, ConnectorsConfig, GoogleWorkspaceAppConnectorConfig
from hushclaw.connectors.google_calendar_sync import GoogleCalendarSyncService
from hushclaw.connectors.manager import ConnectorsManager
from hushclaw.memory.store import MemoryStore
from hushclaw.memory.encryption import INTEGRITY_ERRORS
from hushclaw.secrets import FileSecretStore


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(data_dir=tmp_path / "db")
    yield memory
    memory.close()


@pytest.fixture
def credentials(tmp_path):
    cfg = GoogleWorkspaceAppConnectorConfig(enabled=True, calendar_sync_enabled=True, auth_mode="custom")
    secrets = FileSecretStore(tmp_path / "secrets.json")
    for ref, value in [(cfg.access_token_ref, "old-token"), (cfg.refresh_token_ref, "refresh"),
                       (cfg.client_id_ref, "client"), (cfg.client_secret_ref, "secret")]:
        secrets.set(ref, value)
    return cfg, secrets


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def event(uid="event", **overrides):
    return {
        "id": uid, "summary": "Meeting", "etag": "etag-1",
        "start": {"dateTime": "2026-09-15T09:00:00+08:00"},
        "end": {"dateTime": "2026-09-15T10:00:00+08:00"},
        **overrides,
    }


def test_refresh_after_401_and_retry(credentials):
    cfg, secrets = credentials
    client = GoogleCalendarClient(cfg, secrets)
    denied = urllib.error.HTTPError("https://www.googleapis.com", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=[denied, response({"items": [{"id": "primary"}]})]) as get:
        with patch("hushclaw.app_connectors.google_calendar._form_request", return_value={"access_token": "fresh"}) as refresh:
            assert client.calendars() == [{"id": "primary"}]
    assert refresh.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert get.call_args_list[0].args[0].get_header("Authorization") == "Bearer old-token"
    assert get.call_args_list[1].args[0].get_header("Authorization") == "Bearer fresh"
    assert secrets.get(cfg.access_token_ref) == "fresh"


def test_refresh_token_without_access_token(credentials):
    cfg, secrets = credentials
    secrets.delete(cfg.access_token_ref)
    with patch("urllib.request.urlopen", return_value=response({"items": []})):
        with patch("hushclaw.app_connectors.google_calendar._form_request", return_value={"access_token": "new"}):
            assert GoogleCalendarClient(cfg, secrets).calendars() == []
    assert secrets.get(cfg.access_token_ref) == "new"


def test_managed_token_without_client_reports_reconnect(credentials):
    cfg, secrets = credentials
    cfg.auth_mode = "managed"
    secrets.delete(cfg.client_id_ref)
    denied = urllib.error.HTTPError("https://www.googleapis.com", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=denied):
        with pytest.raises(OAuthError, match="Custom OAuth app"):
            GoogleCalendarClient(cfg, secrets).calendars()


def test_permission_failure_is_actionable_and_does_not_leak_tokens(credentials):
    cfg, secrets = credentials
    denied = urllib.error.HTTPError("https://www.googleapis.com", 403, "Forbidden", {}, None)
    with patch("urllib.request.urlopen", side_effect=denied):
        with pytest.raises(OAuthError, match="calendar.readonly") as failure:
            GoogleCalendarClient(cfg, secrets).calendars()
    assert "old-token" not in str(failure.value)


def test_calendar_and_event_pagination_and_encoded_id(credentials):
    cfg, secrets = credentials
    pages = [response({"items": [{"id": "a"}], "nextPageToken": "second"}),
             response({"items": [{"id": "b"}]}),
             response({"items": [], "nextPageToken": "more"}),
             response({"items": [event()]})]
    with patch("urllib.request.urlopen", side_effect=pages) as get:
        client = GoogleCalendarClient(cfg, secrets)
        assert len(client.calendars()) == 2
        assert len(list(client.events("a/b@example.com", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"))) == 1
    assert parse_qs(urlsplit(get.call_args_list[1].args[0].full_url).query)["pageToken"] == ["second"]
    url = get.call_args_list[3].args[0].full_url
    assert "/a%2Fb%40example.com/events?" in url
    params = parse_qs(urlsplit(url).query)
    assert params["singleEvents"] == ["true"]
    assert params["pageToken"] == ["more"]
    assert "old-token" not in url


def test_repeated_page_token_fails_instead_of_looping(credentials):
    cfg, secrets = credentials
    with patch.object(GoogleCalendarClient, "_get", return_value={"items": [], "nextPageToken": "repeat"}):
        with pytest.raises(ValueError, match="repeated page"):
            GoogleCalendarClient(cfg, secrets).calendars()


def test_event_mapping_preserves_instances_calendars_and_all_day():
    row = GoogleCalendarSyncService._event_row("primary", event("series_20260915"))
    assert row["start_time"] == "2026-09-15T01:00:00Z"
    assert not row["all_day"]
    assert row["event_id"] != GoogleCalendarSyncService._event_row("primary", event("series_20260916"))["event_id"]
    assert row["event_id"] != GoogleCalendarSyncService._event_row("shared", event("series_20260915"))["event_id"]
    moved = GoogleCalendarSyncService._event_row("primary", event("series_20260915", start={"dateTime": "2026-09-16T09:00:00+08:00"}))
    assert row["event_id"] == moved["event_id"]
    all_day = GoogleCalendarSyncService._event_row("primary", event(start={"date": "2026-09-15"}, end={"date": "2026-09-17"}))
    assert all_day["all_day"] and all_day["end_time"] == "2026-09-17"


def test_snapshot_deletes_stale_google_only_and_handles_empty(store):
    local = store.add_calendar_event(title="Local", start_time="2026-09-15", end_time="2026-09-16")
    store.upsert_caldav_event(event_id="caldav:keep", title="CalDAV", start_time="2026-09-15", end_time="2026-09-16")
    first = GoogleCalendarSyncService._event_row("primary", event("old"))
    second = GoogleCalendarSyncService._event_row("primary", event("new"))
    assert store.replace_google_calendar_events([first]) == 1
    assert store.replace_google_calendar_events([second]) == 1
    assert store.get_calendar_event(first["event_id"]) is None
    assert store.get_calendar_event(second["event_id"])["source"] == "google"
    store.replace_google_calendar_events([])
    assert {e["event_id"] for e in store.list_calendar_events()} == {local["event_id"], "caldav:keep"}


def test_snapshot_never_overwrites_local_collision(store):
    local = store.add_calendar_event(title="Local", start_time="2026-09-15", end_time="2026-09-16")
    row = GoogleCalendarSyncService._event_row("primary", event())
    row["event_id"] = local["event_id"]
    assert store.replace_google_calendar_events([row]) == 0
    assert store.get_calendar_event(local["event_id"])["title"] == "Local"


@pytest.mark.asyncio
async def test_sync_works_without_caldav_and_persists_success(credentials, store):
    cfg, secrets = credentials
    service = GoogleCalendarSyncService(cfg, store, secrets)
    with patch.dict("sys.modules", {"caldav": None}):
        with patch("hushclaw.connectors.google_calendar_sync.GoogleCalendarClient") as client:
            client.return_value.calendars.return_value = [{"id": "primary"}]
            client.return_value.events.return_value = [event(), event("cancelled", status="cancelled")]
            assert await service.sync() == 1
    assert service.last_sync > 0 and not service.last_error
    assert len(store.list_calendar_events()) == 1
    assert GoogleCalendarSyncService(cfg, store, secrets).last_sync == service.last_sync // 1


@pytest.mark.asyncio
async def test_partial_fetch_failure_preserves_existing_snapshot(credentials, store):
    cfg, secrets = credentials
    original = GoogleCalendarSyncService._event_row("primary", event("original"))
    store.replace_google_calendar_events([original])
    service = GoogleCalendarSyncService(cfg, store, secrets)
    with patch("hushclaw.connectors.google_calendar_sync.GoogleCalendarClient") as client:
        client.return_value.calendars.return_value = [{"id": "primary"}, {"id": "shared"}]
        client.return_value.events.side_effect = [[event("new")], RuntimeError("second calendar failed")]
        assert await service.sync() == 0
    assert "second calendar failed" in service.last_error
    assert [e["event_id"] for e in store.list_calendar_events()] == [original["event_id"]]
    assert not service.last_sync


@pytest.mark.asyncio
async def test_stop_prevents_snapshot_publication(credentials, store):
    cfg, secrets = credentials
    service = GoogleCalendarSyncService(cfg, store, secrets)
    with patch("hushclaw.connectors.google_calendar_sync.GoogleCalendarClient") as client:
        def stop_during_fetch():
            service._stop_event.set()
            return []
        client.return_value.calendars.side_effect = stop_during_fetch
        with patch.object(store, "replace_google_calendar_events") as replace:
            assert await service.sync() == 0
            replace.assert_not_called()


@pytest.mark.asyncio
async def test_manager_starts_reloads_and_syncs_google(credentials, store):
    cfg, secrets = credentials
    with patch("hushclaw.secrets.get_secret_store", return_value=secrets):
        manager = ConnectorsManager(ConnectorsConfig(), MagicMock(), memory_store=store, google_workspace_config=cfg)
    assert manager._google_calendar_sync is not None
    with patch.object(manager._google_calendar_sync, "sync", new=AsyncMock(return_value=3)):
        assert await manager.force_caldav_sync() == 3
    manager._google_calendar_sync._last_error = "Reconnect Google"
    with patch.object(manager._google_calendar_sync, "sync", new=AsyncMock(return_value=0)):
        with pytest.raises(RuntimeError, match="Reconnect Google"):
            await manager.force_caldav_sync()
    cfg.calendar_sync_enabled = False
    await manager.reload(ConnectorsConfig(), MagicMock(), memory_store=store, google_workspace_config=cfg)
    assert manager._google_calendar_sync is None


def test_unified_config_preserves_google_sync_toggle(tmp_path):
    from hushclaw.connections.config import connections_raw_to_legacy, legacy_to_connections_raw
    from hushclaw.config.loader import _dict_to_config
    raw = {"google_workspace": {"kind": "app", "provider": "google_workspace", "enabled": True, "calendar_sync_enabled": True}}
    assert connections_raw_to_legacy(raw)["app_connectors"]["google_workspace"]["calendar_sync_enabled"] is True
    legacy = connections_raw_to_legacy(raw)
    assert _dict_to_config(legacy).app_connectors.google_workspace.calendar_sync_enabled is True
    assert connections_raw_to_legacy(legacy_to_connections_raw(legacy)) == legacy


def test_oauth_state_expiry_and_replay(credentials):
    from hushclaw.app_connectors.oauth import STATE_PREFIX, complete_oauth
    from hushclaw.config.schema import Config, AppConnectorsConfig
    cfg, secrets = credentials
    secrets.set(STATE_PREFIX + "expired", json.dumps({"connector": "google_workspace", "created": time.time() - 601, "redirect_uri": "http://localhost/callback"}))
    root = Config(app_connectors=AppConnectorsConfig(google_workspace=cfg))
    with pytest.raises(OAuthError, match="expired"):
        complete_oauth("google_workspace", "code", "expired", root, secrets)
    assert not secrets.get(STATE_PREFIX + "expired")
    with pytest.raises(OAuthError, match="missing or expired"):
        complete_oauth("google_workspace", "code", "expired", root, secrets)


def test_snapshot_rolls_back_on_database_error(store):
    original = GoogleCalendarSyncService._event_row("primary", event("original"))
    store.replace_google_calendar_events([original])
    invalid = GoogleCalendarSyncService._event_row("primary", event("invalid"))
    invalid["title"] = None  # NOT NULL constraint
    with pytest.raises(INTEGRITY_ERRORS):
        store.replace_google_calendar_events([invalid])
    assert [row["event_id"] for row in store.list_calendar_events()] == [original["event_id"]]


@pytest.mark.asyncio
async def test_google_oauth_callback_uses_state_without_api_key(credentials):
    from hushclaw.server.http_mixin import HttpMixin
    from hushclaw.config.schema import Config, AppConnectorsConfig
    from hushclaw.app_connectors.oauth import STATE_PREFIX
    cfg, secrets = credentials
    root = Config(app_connectors=AppConnectorsConfig(google_workspace=cfg))

    class Server(HttpMixin):
        _config = SimpleNamespace(api_key="app-key", public_base_url="http://localhost:8765", host="localhost", port=8765)
        _gateway = SimpleNamespace(base_agent=SimpleNamespace(config=root))
        _apply_config = MagicMock()

    server = Server()
    request = SimpleNamespace(headers={"Host": "localhost:8765"})
    secrets.set(STATE_PREFIX + "valid", json.dumps({"connector": "google_workspace", "created": time.time(), "redirect_uri": "http://localhost:8765/oauth/app-connectors/google_workspace/callback"}))
    with patch("hushclaw.secrets.get_secret_store", return_value=secrets):
        with patch("hushclaw.app_connectors.oauth._form_request", return_value={"access_token": "authorized"}):
            with patch("hushclaw.app_connectors.oauth.persist_connector_updates") as persist:
                result = await server._handle_app_connector_oauth(request, "code=code&state=valid", "/oauth/app-connectors/google_workspace/callback")
                assert result.status_code == 200
                persist.assert_called_once()
                invalid = await server._handle_app_connector_oauth(request, "code=code&state=valid", "/oauth/app-connectors/google_workspace/callback")
                assert invalid.status_code == 400
                start = await server._handle_app_connector_oauth(request, "", "/oauth/app-connectors/google_workspace/start")
                assert start.status_code == 401
    assert secrets.get(cfg.access_token_ref) == "authorized"


def test_oauth_callback_persists_enabled_in_normalized_connections(credentials, tmp_path):
    import tomllib
    from hushclaw.app_connectors.oauth import persist_connector_updates
    from hushclaw.connections.config import connections_raw_to_legacy
    path = tmp_path / "hushclaw.toml"
    path.write_text('[app_connectors.google_workspace]\nenabled=false\ncalendar_sync_enabled=true\n'
                    '[connections.google_workspace]\nkind="app"\nprovider="google_workspace"\nenabled=false\ncalendar_sync_enabled=true\n')
    with patch("hushclaw.config.loader.get_config_dir", return_value=tmp_path):
        persist_connector_updates("google_workspace", {"enabled": True, "auth_mode": "custom", "auth_type": "oauth"})
    saved = tomllib.loads(path.read_text())
    google = connections_raw_to_legacy(saved["connections"])["app_connectors"]["google_workspace"]
    assert google["enabled"] and google["calendar_sync_enabled"]


@pytest.mark.asyncio
async def test_saving_sync_setting_round_trips_without_saving_secret_values(credentials, tmp_path):
    import tomllib
    from hushclaw.server.config_handler import handle_save_config
    from hushclaw.connections.config import connections_raw_to_legacy
    cfg, secrets = credentials
    ws = SimpleNamespace(send=AsyncMock())
    with patch("hushclaw.config.loader.get_config_dir", return_value=tmp_path):
        with patch("hushclaw.secrets.get_secret_store", return_value=secrets):
            await handle_save_config(ws, {"config": {"app_connectors": {"google_workspace": {
                "enabled": True, "calendar_sync_enabled": True, "client_secret": "new-secret",
            }}}}, MagicMock())
    saved = tomllib.loads((tmp_path / "hushclaw.toml").read_text())
    assert saved["app_connectors"]["google_workspace"]["calendar_sync_enabled"]
    assert connections_raw_to_legacy(saved["connections"])["app_connectors"]["google_workspace"]["calendar_sync_enabled"]
    assert "new-secret" not in (tmp_path / "hushclaw.toml").read_text()
    assert secrets.get(cfg.client_secret_ref) == "new-secret"
    assert json.loads(ws.send.call_args.args[0])["ok"]
