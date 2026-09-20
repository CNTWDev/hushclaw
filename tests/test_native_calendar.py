"""Native calendar opt-in, snapshot isolation, and mutation boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.connectors.native_calendar import NativeCalendarReader, NativeCalendarSyncService
from hushclaw.memory.store import MemoryStore
from hushclaw.server.calendar_mixin import CalendarMixin, validate_event


@pytest.fixture
def service(tmp_path):
    memory = MemoryStore(data_dir=tmp_path / 'db')
    service = NativeCalendarSyncService(memory)
    service.reader = MagicMock()
    yield service
    memory.close()


def event(**changes):
    return dict(uid='recurring-1', calendar_id='work', calendar_title='Work',
                title='Meeting', start_time='2026-09-20T01:00:00Z',
                end_time='2026-09-20T02:00:00Z', all_day=False, location='', **changes)


def enable(service):
    service._memory.conn.execute("INSERT OR REPLACE INTO calendar_sources VALUES('macos',?)",
        (json.dumps({'enabled': True, 'calendar_ids': ['work']}),))


@pytest.mark.asyncio
async def test_default_does_not_read_build_or_request_permission(service):
    await service.start()
    assert service._task is None
    assert await service.sync() == 0
    service.reader.call.assert_not_called()
    service.reader.build.assert_not_called()
    service.reader.call.return_value = {'permission': 'not_determined'}
    status = await service.status()
    assert not status['enabled']
    service.reader.call.assert_called_once_with('status')
    service.reader.build.assert_not_called()


@pytest.mark.asyncio
async def test_authorization_is_explicit(service):
    service.reader.call.return_value = {'permission': 'denied'}
    await service.status(authorize=True)
    service.reader.build.assert_called_once()
    service.reader.call.assert_called_once_with('authorize')


@pytest.mark.asyncio
async def test_selection_validates_before_saving_and_pause_retains_cache(service):
    service.reader.call.return_value = {'calendars': [{'id': 'work'}], 'permission': 'authorized'}
    for ids in ([], ['unavailable']):
        with pytest.raises(ValueError):
            await service.select(True, ids)
    assert not service.preferences()['enabled']
    service.sync = AsyncMock(return_value=1)
    service.start = AsyncMock()
    result = await service.select(True, ['work', 'work'])
    assert result['calendar_ids'] == ['work']
    service.sync.assert_awaited_once()
    service.start.assert_awaited_once()
    result = await service.select(False, ['work'])
    assert not result['enabled']
    assert result['calendar_ids'] == ['work']


@pytest.mark.asyncio
async def test_snapshot_refresh_preserves_other_sources_and_minimizes_import(service):
    mem = service._memory
    local = mem.add_calendar_event(title='Local', start_time='2026-09-20', end_time='2026-09-21')
    mem.upsert_caldav_event(event_id='caldav:keep', title='Remote', start_time='2026-09-20', end_time='2026-09-21')
    enable(service)
    row = event(notes='private notes', attendees=['private@example.test'])
    service.reader.call.return_value = {'events': [row]}
    assert await service.sync() == 1
    saved = next(e for e in mem.list_calendar_events() if e['source'] == 'macos')
    assert saved['description'] == '' and saved['attendees'] == []
    assert saved['remote_etag'] == 'Work'
    assert saved['event_id'].startswith('macos:')
    assert NativeCalendarSyncService(mem).last_sync > 0
    for action in (lambda: mem.update_calendar_event(saved['event_id'], title='Changed'),
                   lambda: mem.delete_calendar_event(saved['event_id'])):
        with pytest.raises(ValueError): action()
    service.reader.call.return_value = {'events': []}
    assert await service.sync() == 0
    assert {e['event_id'] for e in mem.list_calendar_events()} == {local['event_id'], 'caldav:keep'}


@pytest.mark.asyncio
async def test_failure_or_invalid_snapshot_never_erases_cache(service):
    enable(service)
    service.reader.call.return_value = {'events': [event()]}
    assert await service.sync() == 1
    original = service._memory.list_calendar_events()
    service.reader.call.side_effect = RuntimeError('permission revoked')
    assert await service.sync() == 0
    assert service.last_error == 'permission revoked'
    service.reader.call.side_effect = None
    for bad in ({**event(), 'end_time': 'invalid'}, {**event(), 'calendar_id': 'unselected'}, {'broken': True}):
        service.reader.call.return_value = {'events': [bad]}
        assert await service.sync() == 0
        assert service._memory.list_calendar_events() == original
    def stopped(*args):
        service._stop_event.set()
        return {'events': []}
    service.reader.call.side_effect = stopped
    assert await service.sync() == 0
    assert service._memory.list_calendar_events() == original


def test_reader_status_does_not_install(tmp_path):
    reader = NativeCalendarReader(tmp_path)
    assert reader.call('status') == {'permission': 'not_installed'}
    assert not reader.root.exists()
    with pytest.raises(ValueError): reader.call('delete')


@pytest.mark.asyncio
async def test_unsupported_server_and_correlated_status():
    server = CalendarMixin()
    server._connectors = SimpleNamespace(_native_calendar_sync=None)
    ws = SimpleNamespace(send=AsyncMock())
    await server._handle_native_calendar(ws, {'request_id': 'test'})
    reply = json.loads(ws.send.call_args.args[0])
    assert reply['request_id'] == 'test' and not reply['supported']


def test_local_event_time_validation():
    base = dict(title='Trip', start_time='2026-09-20T01:00:00Z', end_time='2026-09-20T02:00:00Z')
    validate_event(base)
    validate_event({**base, 'all_day': True, 'start_time': '2026-09-20', 'end_time': '2026-09-21'})
    for fields in ({'title': ''}, {'end_time': base['start_time']}, {'start_time': 'not-a-date'},
                   {'start_time': '2026-09-20T01:00:00', 'end_time': '2026-09-20T02:00:00'}):
        with pytest.raises(ValueError): validate_event({**base, **fields})
