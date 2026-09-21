"""Removing a calendar source must not leave cached events or a live writer."""
import asyncio
import json
import threading
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.config.schema import CalendarConfig, ConnectorsConfig
from hushclaw.connectors.caldav_sync import CalDAVSyncService
from hushclaw.connectors.manager import ConnectorsManager
from hushclaw.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(data_dir=tmp_path / 'db')
    yield memory
    memory.close()


def seed(store):
    return {source: store.add_calendar_event(title=source, start_time='2026-09-20',
        end_time='2026-09-21', source=source)['event_id']
        for source in ('local', 'caldav', 'google', 'macos')}


@pytest.mark.asyncio
@pytest.mark.parametrize('delete', [False, True])
async def test_last_source_disable_or_delete_clears_only_its_snapshot_and_cursor(store, delete):
    ids = seed(store)
    cfg = CalendarConfig(enabled=True, url='https://example.test/calendar', username='me')
    manager = ConnectorsManager(ConnectorsConfig(), MagicMock(), calendar_config=cfg, memory_store=store)
    manager.start = AsyncMock()  # Never contact a remote calendar in this test.
    old = manager._caldav_sync
    old._last_sync = 10
    old._persist_sync_state()
    store.save_caldav_collection_state('work', last_ctag='one', last_sync_token='two', last_scan_at=10, last_result_count=1)
    await manager.reload(ConnectorsConfig(), MagicMock(), calendar_config=CalendarConfig() if delete else replace(cfg, enabled=False), memory_store=store)
    assert manager._caldav_sync is None
    assert store.get_calendar_event(ids['caldav']) is None
    assert all(store.get_calendar_event(ids[s]) for s in ('local', 'google', 'macos'))
    assert store.get_caldav_sync_state(old._sync_key) is None
    assert store.get_caldav_collection_state('work') is None
    assert await old.sync() == 0
    await manager.reload(ConnectorsConfig(), MagicMock(), calendar_config=cfg, memory_store=store)
    assert manager._caldav_sync.last_sync == 0


@pytest.mark.asyncio
async def test_unrelated_save_does_not_clear_calendar(store):
    ids = seed(store)
    cfg = CalendarConfig(enabled=True, url='https://example.test/calendar', username='me')
    manager = ConnectorsManager(ConnectorsConfig(), MagicMock(), calendar_config=cfg, memory_store=store)
    manager.start = AsyncMock()
    await manager.reload(ConnectorsConfig(), MagicMock(), calendar_config=replace(cfg, label='New label'), memory_store=store)
    assert all(store.get_calendar_event(eid) for eid in ids.values())


@pytest.mark.asyncio
async def test_saving_already_disabled_legacy_source_cleans_leftover_cache(store):
    ids = seed(store)
    cfg = CalendarConfig(enabled=False, url='https://example.test/calendar', username='me')
    manager = ConnectorsManager(ConnectorsConfig(), MagicMock(), calendar_config=cfg, memory_store=store)
    manager.start = AsyncMock()
    await manager.reload(ConnectorsConfig(), MagicMock(), calendar_config=cfg, memory_store=store)
    assert store.get_calendar_event(ids['caldav']) is None
    assert all(store.get_calendar_event(ids[s]) for s in ('local', 'google', 'macos'))


@pytest.mark.asyncio
async def test_stop_drains_inflight_worker_before_cleanup(store):
    cfg = CalendarConfig(enabled=True, url='https://example.test/calendar', username='me')
    service = CalDAVSyncService(cfg, store)
    entered, release = threading.Event(), threading.Event()
    service._ready_to_sync = lambda: True
    def work(_):
        entered.set()
        assert release.wait(5)
        store.upsert_caldav_event(event_id='caldav:late', title='Late', start_time='2026-09-20', end_time='2026-09-21')
        return 1, {'caldav:late'}
    service._do_sync = work
    sync = asyncio.create_task(service.sync())
    assert await asyncio.to_thread(entered.wait, 5)
    stopped = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert not stopped.done()
    release.set()
    await asyncio.wait_for(stopped, 5)
    await sync
    store.clear_synced_calendar_source('caldav', service._sync_key)
    assert await service.sync() == 0
    assert not store.list_calendar_events()
    assert store.get_caldav_sync_state(service._sync_key) is None


def test_cleanup_rejects_local_or_unknown_source(store):
    ids = seed(store)
    for source in ('local', '', "caldav' OR 1=1 --"):
        with pytest.raises(ValueError):
            store.clear_synced_calendar_source(source)
    assert all(store.get_calendar_event(eid) for eid in ids.values())


@pytest.mark.asyncio
async def test_google_disable_only_clears_google(store):
    from hushclaw.config.schema import GoogleWorkspaceAppConnectorConfig
    ids = seed(store)
    cfg = GoogleWorkspaceAppConnectorConfig(enabled=True, calendar_sync_enabled=True)
    manager = ConnectorsManager(ConnectorsConfig(), MagicMock(), google_workspace_config=cfg, memory_store=store)
    manager.start = AsyncMock()
    await manager.reload(ConnectorsConfig(), MagicMock(), google_workspace_config=replace(cfg, calendar_sync_enabled=False), memory_store=store)
    assert store.get_calendar_event(ids['google']) is None
    assert all(store.get_calendar_event(ids[s]) for s in ('local', 'caldav', 'macos'))


@pytest.mark.asyncio
async def test_removal_roundtrips_empty_accounts_and_preserves_remaining_password(monkeypatch, tmp_path):
    import tomllib
    import hushclaw.config.loader as loader
    from hushclaw.config.writer import dict_to_toml_str
    from hushclaw.server.config_handler import handle_save_config
    monkeypatch.setattr(loader, 'get_config_dir', lambda: tmp_path)
    path = tmp_path / 'hushclaw.toml'
    accounts = [{'url':'https://a.test', 'username':'a', 'password':'secret-a'},
                {'url':'https://b.test', 'username':'b', 'password':'secret-b'}]
    path.write_text(dict_to_toml_str({'calendar': accounts}))
    ws = AsyncMock()
    await handle_save_config(ws, {'config':{'calendar':[{'url':'https://b.test', 'username':'b'}]}}, lambda: None)
    assert json.loads(ws.send.call_args.args[0])['ok']
    assert tomllib.loads(path.read_text())['calendar'][0]['password'] == 'secret-b'
    await handle_save_config(ws, {'config':{'calendar':[]}}, lambda: None)
    raw = tomllib.loads(path.read_text())
    assert raw['calendar'] == []
    assert not any(c.get('provider') == 'calendar' for c in raw.get('connections', {}).values())
    assert loader._dict_to_config(raw).calendars == []
