from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from hushclaw.tools.builtins.local_calendar_tools import _resolve_scope


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
