from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from hushclaw.tools.builtins.local_calendar_tools import _resolve_scope, get_day_agenda


def test_resolve_scope_without_configured_timezone_falls_back_to_local_tz(monkeypatch):
    fake_local = datetime.fromisoformat("2026-04-21T00:30:00+08:00")

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fake_local
            return fake_local.astimezone(tz)

    monkeypatch.setattr("datetime.datetime", FakeDateTime)

    from_utc, to_utc = _resolve_scope(
        "today",
        config=SimpleNamespace(calendar=SimpleNamespace(timezone="")),
        client_now="2026-04-20T16:30:00Z",
    )

    fallback_tz = fake_local.astimezone().tzinfo or timezone.utc
    client_dt = datetime.fromisoformat("2026-04-20T16:30:00+00:00").astimezone(fallback_tz)
    expected_start = client_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    expected_end = expected_start + timedelta(days=1)

    assert from_utc == expected_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert to_utc == expected_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_get_day_agenda_formats_times_in_effective_timezone(monkeypatch):
    fake_tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        "hushclaw.tools.builtins.local_calendar_tools._resolve_effective_timezone",
        lambda config=None: (fake_tz, "", str(fake_tz), "server_local"),
    )

    class _Mem:
        def list_calendar_events(self, from_time=None, to_time=None):
            return [{
                "event_id": "evt-1",
                "title": "Morning sync",
                "start_time": "2026-04-20T02:00:00Z",
                "end_time": "2026-04-20T02:30:00Z",
                "all_day": False,
                "location": "",
            }]

    out = get_day_agenda(
        scope="today",
        _memory_store=_Mem(),
        _config=SimpleNamespace(calendar=SimpleNamespace(timezone="")),
        _client_now="2026-04-20T16:30:00Z",
    )

    assert not out.is_error
    assert "10:00-10:30" not in out.content
    assert "10:00–10:30" in out.content
