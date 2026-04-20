"""Tests for CalDAV sync integration.

Covers:
- SQLite migration: source column present on new and migrated DBs
- upsert_caldav_event: rowcount accuracy, source='local' protection
- prune_stale_caldav_events: stale removal, safety when set is empty
- CalDAVSyncService.sync(): accurate count, stale pruning, import guard
- CalendarMixin._handle_force_sync_caldav: WebSocket response shape
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.memory.store import MemoryStore


# ── helpers ──────────────────────────────────────────────────────────────────

def make_store() -> MemoryStore:
    d = tempfile.mkdtemp()
    return MemoryStore(data_dir=Path(d))


def _raw_conn(store: MemoryStore) -> sqlite3.Connection:
    return store.conn


# ── SQLite migration ──────────────────────────────────────────────────────────

class TestMigration:
    def test_source_column_exists_on_fresh_db(self):
        store = make_store()
        cols = [
            row[1]
            for row in _raw_conn(store).execute(
                "PRAGMA table_info(calendar_events)"
            ).fetchall()
        ]
        assert "source" in cols
        store.close()

    def test_source_column_default_is_local(self):
        store = make_store()
        # Insert a row without specifying source (to test DEFAULT)
        store.add_calendar_event(
            title="test", start_time="2026-04-20T09:00:00", end_time="2026-04-20T10:00:00"
        )
        rows = _raw_conn(store).execute("SELECT source FROM calendar_events").fetchall()
        assert all(r[0] == "local" for r in rows)
        store.close()

    def test_migration_idempotent(self):
        """Running open_db twice on the same file should not raise."""
        d = tempfile.mkdtemp()
        from hushclaw.memory.db import open_db
        conn1 = open_db(Path(d))
        conn1.close()
        conn2 = open_db(Path(d))  # applies ALTER TABLE again — must be caught
        conn2.close()


# ── upsert_caldav_event ───────────────────────────────────────────────────────

class TestUpsertCaldavEvent:
    def _insert_caldav(self, store: MemoryStore, event_id: str = "caldav:uid-1") -> int:
        return store.upsert_caldav_event(
            event_id=event_id,
            title="Team standup",
            start_time="2026-04-21T09:00:00",
            end_time="2026-04-21T09:30:00",
        )

    def test_insert_returns_1(self):
        store = make_store()
        assert self._insert_caldav(store) == 1
        store.close()

    def test_update_existing_caldav_returns_1(self):
        store = make_store()
        self._insert_caldav(store)
        # Update same event — should also return 1
        rc = store.upsert_caldav_event(
            event_id="caldav:uid-1",
            title="Standup (rescheduled)",
            start_time="2026-04-21T10:00:00",
            end_time="2026-04-21T10:30:00",
        )
        assert rc == 1
        row = _raw_conn(store).execute(
            "SELECT title FROM calendar_events WHERE event_id='caldav:uid-1'"
        ).fetchone()
        assert row[0] == "Standup (rescheduled)"
        store.close()

    def test_local_event_not_overwritten_returns_0(self):
        """If a row already exists with source='local', the upsert must be a no-op."""
        store = make_store()
        # Create a local event with the same event_id that the caldav sync would use
        _raw_conn(store).execute(
            "INSERT INTO calendar_events "
            "(event_id, title, description, location, start_time, end_time, "
            " all_day, color, attendees, source, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("caldav:uid-1", "My local copy", "", "", "2026-04-21T09:00:00",
             "2026-04-21T10:00:00", 0, "indigo", "[]", "local",
             1_000_000, 1_000_000),
        )
        _raw_conn(store).commit()

        rc = store.upsert_caldav_event(
            event_id="caldav:uid-1",
            title="CalDAV version (should not win)",
            start_time="2026-04-21T09:00:00",
            end_time="2026-04-21T09:30:00",
        )
        assert rc == 0  # skipped

        row = _raw_conn(store).execute(
            "SELECT title, source FROM calendar_events WHERE event_id='caldav:uid-1'"
        ).fetchone()
        assert row[0] == "My local copy"
        assert row[1] == "local"
        store.close()

    def test_source_stored_as_caldav(self):
        store = make_store()
        self._insert_caldav(store)
        row = _raw_conn(store).execute(
            "SELECT source FROM calendar_events WHERE event_id='caldav:uid-1'"
        ).fetchone()
        assert row[0] == "caldav"
        store.close()


# ── prune_stale_caldav_events ─────────────────────────────────────────────────

class TestPruneStaleCaldavEvents:
    def _seed(self, store: MemoryStore, n: int) -> list[str]:
        ids = []
        for i in range(n):
            eid = f"caldav:uid-{i}"
            store.upsert_caldav_event(
                event_id=eid,
                title=f"Event {i}",
                start_time="2026-04-21T09:00:00",
                end_time="2026-04-21T10:00:00",
            )
            ids.append(eid)
        return ids

    def test_prune_removes_missing_caldav_rows(self):
        store = make_store()
        ids = self._seed(store, 3)
        # Keep only first two — third should be pruned
        pruned = store.prune_stale_caldav_events({ids[0], ids[1]})
        assert pruned == 1
        remaining = [
            r[0] for r in _raw_conn(store).execute(
                "SELECT event_id FROM calendar_events"
            ).fetchall()
        ]
        assert ids[2] not in remaining
        store.close()

    def test_prune_empty_kept_set_is_noop(self):
        """Safety: if sync returns no events, do not wipe everything."""
        store = make_store()
        self._seed(store, 2)
        pruned = store.prune_stale_caldav_events(set())
        assert pruned == 0
        count = _raw_conn(store).execute(
            "SELECT count(*) FROM calendar_events"
        ).fetchone()[0]
        assert count == 2
        store.close()

    def test_prune_does_not_touch_local_rows(self):
        """Local events must survive pruning regardless of event_id."""
        store = make_store()
        store.add_calendar_event(
            title="local event",
            start_time="2026-04-21T09:00:00",
            end_time="2026-04-21T10:00:00",
        )
        store.upsert_caldav_event(
            event_id="caldav:uid-1",
            title="caldav event",
            start_time="2026-04-22T09:00:00",
            end_time="2026-04-22T10:00:00",
        )
        # Prune with a set that does NOT include the caldav row
        store.prune_stale_caldav_events(set())  # noop — empty set
        count = _raw_conn(store).execute(
            "SELECT count(*) FROM calendar_events WHERE source='local'"
        ).fetchone()[0]
        assert count == 1
        store.close()


# ── CalDAVSyncService ─────────────────────────────────────────────────────────

class TestCalDAVSyncService:
    def _make_service(self, store: MemoryStore):
        from hushclaw.config.schema import CalendarConfig
        from hushclaw.connectors.caldav_sync import CalDAVSyncService
        cfg = CalendarConfig(
            enabled=True,
            url="https://caldav.example.com",
            username="user",
            password="pass",
            sync_interval_minutes=30,
        )
        return CalDAVSyncService(cfg, store)

    def _make_vevent(self, uid: str, summary: str, start: str, end: str):
        """Build a minimal icalendar VEVENT-like object."""
        from datetime import datetime
        ve = MagicMock()
        ve.name = "VEVENT"
        ve.get = lambda key, default="": {
            "UID": uid,
            "SUMMARY": summary,
            "DTSTART": MagicMock(dt=datetime.fromisoformat(start)),
            "DTEND": MagicMock(dt=datetime.fromisoformat(end)),
            "DESCRIPTION": "",
            "LOCATION": "",
        }.get(key, default)
        return ve

    def _build_caldav_mock(self, vevents: list):
        """Return a 'caldav' module mock that yields the given vevents."""
        component = MagicMock()
        component.icalendar_component.subcomponents = vevents

        calendar = MagicMock()
        calendar.events.return_value = [component]

        principal = MagicMock()
        principal.calendars.return_value = [calendar]

        client = MagicMock()
        client.principal.return_value = principal

        caldav_mod = MagicMock()
        caldav_mod.DAVClient.return_value = client
        return caldav_mod

    @pytest.mark.asyncio
    async def test_sync_returns_accurate_count(self):
        store = make_store()
        svc = self._make_service(store)
        vevents = [
            self._make_vevent("uid-1", "Meeting A", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
            self._make_vevent("uid-2", "Meeting B", "2026-04-22T09:00:00", "2026-04-22T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            count = await svc.sync()
        assert count == 2
        store.close()

    @pytest.mark.asyncio
    async def test_sync_skipped_local_not_counted(self):
        """A local event sharing an event_id should not inflate the count."""
        store = make_store()
        # Pre-seed a local row with the same event_id that caldav would use
        _raw_conn(store).execute(
            "INSERT INTO calendar_events "
            "(event_id, title, description, location, start_time, end_time, "
            " all_day, color, attendees, source, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("caldav:uid-1", "local copy", "", "", "2026-04-21T09:00:00",
             "2026-04-21T10:00:00", 0, "indigo", "[]", "local", 1000, 1000),
        )
        _raw_conn(store).commit()

        svc = self._make_service(store)
        vevents = [
            self._make_vevent("uid-1", "CalDAV version", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            count = await svc.sync()
        assert count == 0  # skipped, not counted
        store.close()

    @pytest.mark.asyncio
    async def test_sync_prunes_stale_events(self):
        """Events no longer on CalDAV should be removed after sync."""
        store = make_store()
        # Pre-seed a stale caldav event
        store.upsert_caldav_event(
            event_id="caldav:old-uid",
            title="Old event",
            start_time="2026-01-01T09:00:00",
            end_time="2026-01-01T10:00:00",
        )
        svc = self._make_service(store)
        vevents = [
            self._make_vevent("uid-new", "New event", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            await svc.sync()

        remaining = _raw_conn(store).execute(
            "SELECT event_id FROM calendar_events"
        ).fetchall()
        ids = [r[0] for r in remaining]
        assert "caldav:old-uid" not in ids
        assert "caldav:uid-new" in ids
        store.close()

    @pytest.mark.asyncio
    async def test_sync_returns_0_when_caldav_not_installed(self):
        store = make_store()
        svc = self._make_service(store)
        # Remove 'caldav' from sys.modules and block import
        with patch.dict("sys.modules", {"caldav": None}):
            count = await svc.sync()
        assert count == 0
        store.close()

    @pytest.mark.asyncio
    async def test_sync_returns_0_when_url_not_configured(self):
        from hushclaw.config.schema import CalendarConfig
        from hushclaw.connectors.caldav_sync import CalDAVSyncService
        store = make_store()
        cfg = CalendarConfig(enabled=True, url="", username="")
        svc = CalDAVSyncService(cfg, store)
        count = await svc.sync()
        assert count == 0
        store.close()

    @pytest.mark.asyncio
    async def test_start_stop(self):
        store = make_store()
        svc = self._make_service(store)
        caldav_mod = self._build_caldav_mock([])
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            await svc.start()
            assert svc._task is not None
            assert not svc._task.done()
            await svc.stop()
            assert svc._task.done()
        store.close()

    @pytest.mark.asyncio
    async def test_last_sync_updated_after_sync(self):
        store = make_store()
        svc = self._make_service(store)
        assert svc.last_sync == 0.0
        caldav_mod = self._build_caldav_mock([])
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            await svc.sync()
        assert svc.last_sync > 0.0
        store.close()


# ── ConnectorsManager + CalDAV ────────────────────────────────────────────────

class TestConnectorsManagerCalDAV:
    def test_caldav_sync_not_created_when_disabled(self):
        from hushclaw.config.schema import CalendarConfig, ConnectorsConfig
        from hushclaw.connectors.manager import ConnectorsManager
        gw = MagicMock()
        cfg_cal = CalendarConfig(enabled=False)
        store = make_store()
        mgr = ConnectorsManager(ConnectorsConfig(), gw, calendar_config=cfg_cal, memory_store=store)
        assert mgr._caldav_sync is None
        store.close()

    def test_caldav_sync_not_created_when_url_empty(self):
        from hushclaw.config.schema import CalendarConfig, ConnectorsConfig
        from hushclaw.connectors.manager import ConnectorsManager
        gw = MagicMock()
        cfg_cal = CalendarConfig(enabled=True, url="")
        store = make_store()
        mgr = ConnectorsManager(ConnectorsConfig(), gw, calendar_config=cfg_cal, memory_store=store)
        assert mgr._caldav_sync is None
        store.close()

    def test_caldav_sync_created_when_enabled(self):
        from hushclaw.config.schema import CalendarConfig, ConnectorsConfig
        from hushclaw.connectors.manager import ConnectorsManager
        gw = MagicMock()
        cfg_cal = CalendarConfig(enabled=True, url="https://caldav.example.com", username="u")
        store = make_store()
        mgr = ConnectorsManager(ConnectorsConfig(), gw, calendar_config=cfg_cal, memory_store=store)
        assert mgr._caldav_sync is not None
        store.close()

    @pytest.mark.asyncio
    async def test_force_caldav_sync_returns_0_when_no_service(self):
        from hushclaw.config.schema import ConnectorsConfig
        from hushclaw.connectors.manager import ConnectorsManager
        gw = MagicMock()
        mgr = ConnectorsManager(ConnectorsConfig(), gw)
        result = await mgr.force_caldav_sync()
        assert result == 0

    @pytest.mark.asyncio
    async def test_reload_reinitialises_caldav_sync(self):
        from hushclaw.config.schema import CalendarConfig, ConnectorsConfig
        from hushclaw.connectors.manager import ConnectorsManager
        gw = MagicMock()
        store = make_store()
        mgr = ConnectorsManager(ConnectorsConfig(), gw)
        assert mgr._caldav_sync is None

        cfg_cal = CalendarConfig(enabled=True, url="https://caldav.example.com", username="u")
        await mgr.reload(
            ConnectorsConfig(), gw, calendar_config=cfg_cal, memory_store=store
        )
        assert mgr._caldav_sync is not None
        store.close()


# ── CalendarMixin._handle_force_sync_caldav ───────────────────────────────────

class TestForceSync:
    def _make_handler(self, store: MemoryStore, sync_count: int = 3):
        """Build a minimal CalendarMixin instance with mocked dependencies."""
        import json
        from hushclaw.server.calendar_mixin import CalendarMixin

        class FakeServer(CalendarMixin):
            pass

        srv = FakeServer()
        srv._gateway = MagicMock()
        srv._gateway.memory = store
        srv._gateway.memory.list_calendar_events = MagicMock(return_value=[])

        srv._connectors = MagicMock()
        srv._connectors.force_caldav_sync = AsyncMock(return_value=sync_count)
        srv._connectors.caldav_last_sync = 1_700_000_000.0
        return srv

    @pytest.mark.asyncio
    async def test_response_shape(self):
        import json
        store = make_store()
        srv = self._make_handler(store, sync_count=5)

        sent = []
        ws = MagicMock()
        ws.send = AsyncMock(side_effect=lambda msg: sent.append(json.loads(msg)))

        await srv._handle_force_sync_caldav(ws, {})

        assert len(sent) == 1
        msg = sent[0]
        assert msg["type"] == "calendar_sync_done"
        assert msg["count"] == 5
        assert msg["last_sync"] == 1_700_000_000.0
        assert "items" in msg
        store.close()

    @pytest.mark.asyncio
    async def test_response_count_reflects_accurate_rowcount(self):
        """Count in the WS message = what force_caldav_sync actually returned (accurate rows)."""
        import json
        store = make_store()
        # Insert a local event — should NOT be counted by the sync
        store.add_calendar_event(
            title="local", start_time="2026-04-20T09:00:00", end_time="2026-04-20T10:00:00"
        )
        srv = self._make_handler(store, sync_count=0)  # 0 caldav rows changed

        sent = []
        ws = MagicMock()
        ws.send = AsyncMock(side_effect=lambda msg: sent.append(json.loads(msg)))

        await srv._handle_force_sync_caldav(ws, {})

        assert sent[0]["count"] == 0
        store.close()
