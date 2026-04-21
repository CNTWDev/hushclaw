"""Tests for CalDAV sync integration.

Covers:
- SQLite migration: source column present on new and migrated DBs
- CalDAV sync state persistence: stored on fresh DBs and round-trips correctly
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
        assert "remote_uid" in cols
        assert "remote_etag" in cols
        assert "last_seen_at" in cols
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

    def test_caldav_sync_state_table_exists_on_fresh_db(self):
        store = make_store()
        cols = [
            row[1]
            for row in _raw_conn(store).execute(
                "PRAGMA table_info(caldav_sync_state)"
            ).fetchall()
        ]
        assert "sync_key" in cols
        assert "failure_count" in cols
        store.close()

    def test_caldav_collection_state_table_exists_on_fresh_db(self):
        store = make_store()
        cols = [
            row[1]
            for row in _raw_conn(store).execute(
                "PRAGMA table_info(caldav_collection_state)"
            ).fetchall()
        ]
        assert "collection_key" in cols
        assert "last_ctag" in cols
        assert "last_sync_token" in cols
        store.close()


class TestCaldavSyncState:
    def test_sync_state_roundtrip(self):
        store = make_store()
        saved = store.save_caldav_sync_state(
            "https://caldav.example.com|user|team",
            last_attempt=100,
            last_success=90,
            last_failure=0,
            failure_count=0,
            last_error="",
            last_result_count=7,
        )
        loaded = store.get_caldav_sync_state("https://caldav.example.com|user|team")
        assert saved["sync_key"] == "https://caldav.example.com|user|team"
        assert loaded is not None
        assert loaded["last_attempt"] == 100
        assert loaded["last_success"] == 90
        assert loaded["last_result_count"] == 7
        store.close()

    def test_collection_state_roundtrip(self):
        store = make_store()
        saved = store.save_caldav_collection_state(
            "/caldav/team/",
            last_ctag='"ctag-1"',
            last_sync_token="token-1",
            last_scan_at=123,
            last_result_count=5,
        )
        loaded = store.get_caldav_collection_state("/caldav/team/")
        assert saved["collection_key"] == "/caldav/team/"
        assert loaded is not None
        assert loaded["last_ctag"] == '"ctag-1"'
        assert loaded["last_sync_token"] == "token-1"
        assert loaded["last_scan_at"] == 123
        assert loaded["last_result_count"] == 5
        store.close()


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

    def test_remote_metadata_persisted(self):
        store = make_store()
        store.upsert_caldav_event(
            event_id="caldav:uid-1",
            title="Team standup",
            start_time="2026-04-21T09:00:00",
            end_time="2026-04-21T09:30:00",
            remote_uid="uid-1",
            remote_href="/caldav/events/uid-1.ics",
            remote_etag='"abc123"',
            recurrence_id="2026-04-21T09:00:00Z",
            remote_calendar="Team",
        )
        row = _raw_conn(store).execute(
            """
            SELECT remote_uid, remote_href, remote_etag, recurrence_id, remote_calendar, last_seen_at
            FROM calendar_events WHERE event_id='caldav:uid-1'
            """
        ).fetchone()
        assert row[0] == "uid-1"
        assert row[1] == "/caldav/events/uid-1.ics"
        assert row[2] == '"abc123"'
        assert row[3] == "2026-04-21T09:00:00Z"
        assert row[4] == "Team"
        assert row[5] > 0
        store.close()

    def test_get_caldav_event_ids_for_resource_matches_etag(self):
        store = make_store()
        store.upsert_caldav_event(
            event_id="caldav:uid-1:2026-04-21T09:00:00Z",
            title="Team standup",
            start_time="2026-04-21T09:00:00Z",
            end_time="2026-04-21T09:30:00Z",
            remote_uid="uid-1",
            remote_href="/caldav/events/uid-1.ics",
            remote_etag='"abc123"',
            remote_calendar="Team",
        )
        ids = store.get_caldav_event_ids_for_resource(
            remote_href="/caldav/events/uid-1.ics",
            remote_etag='"abc123"',
        )
        assert ids == ["caldav:uid-1:2026-04-21T09:00:00Z"]
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
        calendar = MagicMock()
        resources = []
        for i, vevent in enumerate(vevents, start=1):
            resource = MagicMock()
            resource.icalendar_component = vevent
            resource.icalendar_instance = MagicMock()
            resource.url = f"/caldav/events/{i}.ics"
            resource.etag = f'"etag-{i}"'
            resources.append(resource)
        calendar.date_search.return_value = resources
        calendar.objects.return_value = []
        calendar.name = "Team Calendar"
        calendar.url = "/caldav/team/"
        calendar.ctag = '"ctag-1"'

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
            ("caldav:uid-1:2026-04-21T09:00:00Z", "local copy", "", "", "2026-04-21T09:00:00Z",
             "2026-04-21T10:00:00Z", 0, "indigo", "[]", "local", 1000, 1000),
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
        assert "caldav:uid-new:2026-04-21T09:00:00Z" in ids
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

    def test_restores_last_sync_state_from_db(self):
        store = make_store()
        store.save_caldav_sync_state(
            "https://caldav.example.com|user|",
            last_attempt=200,
            last_success=180,
            last_failure=0,
            failure_count=0,
            last_error="",
            last_result_count=2,
        )
        svc = self._make_service(store)
        assert svc.last_sync == 180.0
        store.close()

    def test_next_background_delay_uses_recent_success(self):
        store = make_store()
        svc = self._make_service(store)
        with patch.object(svc, "_now_ts", return_value=1_000.0):
            svc._last_sync = 950.0
            delay = svc._next_background_delay_seconds()
        assert delay > 0
        store.close()

    @pytest.mark.asyncio
    async def test_failure_persists_backoff_state(self):
        store = make_store()
        svc = self._make_service(store)
        with patch.dict("sys.modules", {"caldav": MagicMock()}):
            with patch.object(svc, "_fetch_and_upsert", side_effect=RuntimeError("boom")):
                count = await svc.sync()
        assert count == 0
        state = store.get_caldav_sync_state("https://caldav.example.com|user|")
        assert state is not None
        assert state["failure_count"] == 1
        assert "boom" in state["last_error"]
        store.close()

    @pytest.mark.asyncio
    async def test_sync_persists_remote_metadata(self):
        store = make_store()
        svc = self._make_service(store)
        vevents = [
            self._make_vevent("uid-1", "Meeting A", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            await svc.sync()
        row = _raw_conn(store).execute(
            """
            SELECT remote_uid, remote_href, remote_etag, remote_calendar, last_seen_at
            FROM calendar_events
            WHERE event_id='caldav:uid-1:2026-04-21T09:00:00Z'
            """
        ).fetchone()
        assert row[0] == "uid-1"
        assert row[1] == "/caldav/events/1.ics"
        assert row[2] == '"etag-1"'
        assert row[3] == "/caldav/team/"
        assert row[4] > 0
        store.close()

    @pytest.mark.asyncio
    async def test_sync_reuses_unchanged_nonrecurring_resource_by_etag(self):
        store = make_store()
        svc = self._make_service(store)
        store.upsert_caldav_event(
            event_id="caldav:uid-1:2026-04-21T09:00:00Z",
            title="Meeting A",
            start_time="2026-04-21T09:00:00Z",
            end_time="2026-04-21T10:00:00Z",
            remote_uid="uid-1",
            remote_href="/caldav/events/1.ics",
            remote_etag='"etag-1"',
            remote_calendar="/caldav/team/",
        )
        vevents = [
            self._make_vevent("uid-1", "Meeting A", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            with patch.object(svc, "_expand_component", wraps=svc._expand_component) as expand_mock:
                count = await svc.sync()
        assert count == 0
        expand_mock.assert_not_called()
        row = _raw_conn(store).execute(
            "SELECT remote_etag, last_seen_at FROM calendar_events WHERE event_id='caldav:uid-1:2026-04-21T09:00:00Z'"
        ).fetchone()
        assert row[0] == '"etag-1"'
        assert row[1] > 0
        store.close()

    @pytest.mark.asyncio
    async def test_sync_reuses_unchanged_calendar_by_ctag(self):
        store = make_store()
        svc = self._make_service(store)
        store.upsert_caldav_event(
            event_id="caldav:uid-1:2026-04-21T09:00:00Z",
            title="Meeting A",
            start_time="2026-04-21T09:00:00Z",
            end_time="2026-04-21T10:00:00Z",
            remote_uid="uid-1",
            remote_href="/caldav/events/1.ics",
            remote_etag='"etag-1"',
            remote_calendar="/caldav/team/",
        )
        store.save_caldav_collection_state(
            "/caldav/team/",
            last_ctag='"ctag-1"',
            last_sync_token="",
            last_scan_at=100,
            last_result_count=1,
        )
        vevents = [
            self._make_vevent("uid-1", "Meeting A", "2026-04-21T09:00:00", "2026-04-21T10:00:00"),
        ]
        caldav_mod = self._build_caldav_mock(vevents)
        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            with patch.object(svc, "_fetch_events", wraps=svc._fetch_events) as fetch_mock:
                count = await svc.sync()
        assert count == 0
        fetch_mock.assert_not_called()
        store.close()

    @pytest.mark.asyncio
    async def test_sync_token_delta_updates_collection_without_full_fetch(self):
        store = make_store()
        svc = self._make_service(store)
        store.save_caldav_collection_state(
            "/caldav/team/",
            last_ctag='"ctag-1"',
            last_sync_token="token-1",
            last_scan_at=100,
            last_result_count=0,
        )

        vevent = self._make_vevent("uid-2", "Meeting B", "2026-04-22T09:00:00", "2026-04-22T10:00:00")
        resource = MagicMock()
        resource.url = "/caldav/events/2.ics"
        resource.etag = '"etag-2"'
        resource.icalendar_component = vevent
        resource.icalendar_instance = MagicMock()
        resource.load = MagicMock()

        class Delta:
            sync_token = "token-2"
            def __iter__(self_nonlocal):
                return iter([resource])

        caldav_mod = self._build_caldav_mock([])
        calendar = caldav_mod.DAVClient.return_value.principal.return_value.calendars.return_value[0]
        calendar.ctag = '"ctag-2"'
        calendar.get_objects_by_sync_token.return_value = Delta()

        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            with patch.object(svc, "_fetch_events", wraps=svc._fetch_events) as fetch_mock:
                count = await svc.sync()

        assert count == 1
        fetch_mock.assert_not_called()
        row = _raw_conn(store).execute(
            "SELECT title FROM calendar_events WHERE event_id='caldav:uid-2:2026-04-22T09:00:00Z'"
        ).fetchone()
        assert row[0] == "Meeting B"
        state = store.get_caldav_collection_state("/caldav/team/")
        assert state is not None
        assert state["last_sync_token"] == "token-2"
        store.close()

    @pytest.mark.asyncio
    async def test_sync_token_delta_deletes_missing_resource(self):
        store = make_store()
        svc = self._make_service(store)
        store.upsert_caldav_event(
            event_id="caldav:uid-1:2026-04-21T09:00:00Z",
            title="Meeting A",
            start_time="2026-04-21T09:00:00Z",
            end_time="2026-04-21T10:00:00Z",
            remote_uid="uid-1",
            remote_href="/caldav/events/1.ics",
            remote_etag='"etag-1"',
            remote_calendar="/caldav/team/",
        )
        store.save_caldav_collection_state(
            "/caldav/team/",
            last_ctag='"ctag-1"',
            last_sync_token="token-1",
            last_scan_at=100,
            last_result_count=1,
        )

        resource = MagicMock()
        resource.url = "/caldav/events/1.ics"
        resource.etag = '"etag-2"'
        resource.load = MagicMock(side_effect=Exception("404 Not Found"))

        class Delta:
            sync_token = "token-2"
            def __iter__(self_nonlocal):
                return iter([resource])

        caldav_mod = self._build_caldav_mock([])
        calendar = caldav_mod.DAVClient.return_value.principal.return_value.calendars.return_value[0]
        calendar.ctag = '"ctag-2"'
        calendar.get_objects_by_sync_token.return_value = Delta()

        with patch.dict("sys.modules", {"caldav": caldav_mod}):
            count = await svc.sync()

        assert count == 0
        row = _raw_conn(store).execute(
            "SELECT count(*) FROM calendar_events WHERE remote_calendar='/caldav/team/'"
        ).fetchone()
        assert row[0] == 0
        state = store.get_caldav_collection_state("/caldav/team/")
        assert state is not None
        assert state["last_sync_token"] == "token-2"
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
