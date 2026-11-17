from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.integrations.google_calendar import (
    CalendarUnavailableError,
    GoogleCalendarClient,
    SyncTokenExpiredError,
)
from app.operations.holiday_sync import (
    build_holiday_rows,
    discover_uae_holiday_calendar,
    event_dates,
    stale_row_ids,
    sync_uae_holidays,
    sync_window,
)


UAE_CALENDAR = "en.ae.official#holiday@group.v.calendar.google.com"


class _Calendar:
    def __init__(self, events=None, calendars=None, metadata=None):
        self.events = events or []
        self.calendars = calendars or []
        self.metadata = metadata or {
            "id": UAE_CALENDAR,
            "summary": "Holidays in United Arab Emirates",
        }
        self.calls: list[str | None] = []

    def list_calendars(self):
        return self.calendars

    def get_calendar(self, _calendar_id):
        return self.metadata

    def list_events(self, _calendar_id, **kwargs):
        self.calls.append(kwargs.get("sync_token"))
        if kwargs.get("sync_token") == "expired":
            raise SyncTokenExpiredError("expired")
        return self.events, "next-token"


class _Query:
    def __init__(self, state, table, operation="select"):
        self.state = state
        self.table = table
        self.operation = operation
        self.filters = {}
        self.ids = None
        self.payload = None

    def select(self, _columns):
        self.operation = "select"
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def gte(self, column, value):
        self.filters[column] = value
        return self

    def lte(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, _value):
        return self

    def in_(self, _column, values):
        self.ids = values
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def upsert(self, rows, **_kwargs):
        self.operation = "upsert"
        self.payload = rows
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def execute(self):
        if self.operation == "select":
            if self.table == "clinic_holiday_sync":
                return SimpleNamespace(data=[self.state["sync"]])
            return SimpleNamespace(data=list(self.state["existing"]))
        if self.operation == "upsert":
            self.state["upserts"].extend(self.payload)
            return SimpleNamespace(data=self.payload)
        if self.operation == "delete":
            self.state["deleted"].extend(self.ids or [])
            return SimpleNamespace(data=[])
        if self.operation == "update":
            self.state["sync"].update(self.payload)
            self.state["updates"].append(self.payload)
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self, sync=None, existing=None):
        self.state = {
            "sync": sync or {"calendar_id": None, "sync_token": None},
            "existing": existing or [],
            "upserts": [],
            "deleted": [],
            "updates": [],
        }

    def rpc(self, _name, _payload):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[True]))

    def table(self, name):
        return _Query(self.state, name)


def test_event_dates_handles_all_day_multi_day_events() -> None:
    event = {
        "start": {"date": "2026-12-02"},
        "end": {"date": "2026-12-04"},  # exclusive
    }
    assert event_dates(event) == [date(2026, 12, 2), date(2026, 12, 3)]


def test_event_dates_handles_timed_events_in_dubai_timezone() -> None:
    event = {
        "start": {"dateTime": "2027-03-09T21:00:00Z"},
        "end": {"dateTime": "2027-03-09T22:00:00Z"},
    }
    assert event_dates(event) == [date(2027, 3, 10)]


def test_sync_window_uses_uae_date_not_server_date() -> None:
    now = datetime(2026, 11, 17, 20, 30, tzinfo=timezone.utc)
    start, end = sync_window(now)
    assert start == date(2025, 11, 18)
    assert end == date(2029, 11, 17)


def test_discovery_prefers_matching_calendar_list_entry() -> None:
    calendar = _Calendar(
        calendars=[
            {
                "id": UAE_CALENDAR,
                "summary": "Holidays in United Arab Emirates",
            }
        ]
    )
    calendar_id, _ = discover_uae_holiday_calendar(calendar, "fallback")
    assert calendar_id == UAE_CALENDAR


def test_discovery_falls_back_to_configured_calendar() -> None:
    calendar = _Calendar(calendars=[])
    calendar_id, metadata = discover_uae_holiday_calendar(calendar, UAE_CALENDAR)
    assert calendar_id == UAE_CALENDAR
    assert "United Arab Emirates" in metadata["summary"]


def test_discovery_rejects_non_uae_calendar() -> None:
    calendar = _Calendar(
        calendars=[],
        metadata={"id": "other", "summary": "Personal calendar"},
    )
    with pytest.raises(CalendarUnavailableError):
        discover_uae_holiday_calendar(calendar, "other")


def test_build_rows_and_stale_detection_handle_modifications_and_cancellations() -> None:
    events = [
        {
            "id": "eid",
            "status": "confirmed",
            "summary": "Eid Al Fitr",
            "start": {"date": "2027-03-10"},
            "end": {"date": "2027-03-12"},
            "updated": "2027-02-01T10:00:00Z",
        },
        {
            "id": "cancelled-event",
            "status": "cancelled",
        },
    ]
    rows, active_ids, cancelled_ids, valid_pairs, coverage = build_holiday_rows(
        "clinic",
        UAE_CALENDAR,
        events,
        datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    assert {row["holiday_date"] for row in rows} == {"2027-03-10", "2027-03-11"}
    assert active_ids == {"eid"}
    assert cancelled_ids == {"cancelled-event"}
    assert coverage == date(2027, 3, 11)

    existing = [
        {"id": "a", "google_event_id": "eid", "holiday_date": "2027-03-10"},
        {"id": "b", "google_event_id": "eid", "holiday_date": "2027-03-11"},
        {"id": "c", "google_event_id": "eid", "holiday_date": "2027-03-12"},
        {"id": "d", "google_event_id": "cancelled-event", "holiday_date": "2027-05-01"},
        {"id": "e", "google_event_id": "other", "holiday_date": "2027-06-01"},
    ]
    assert set(stale_row_ids(existing, valid_pairs, active_ids, cancelled_ids, full_sync=True)) == {
        "c", "d", "e",
    }
    assert set(stale_row_ids(existing, valid_pairs, active_ids, cancelled_ids, full_sync=False)) == {
        "c", "d",
    }


def test_sync_upserts_google_events_and_records_state() -> None:
    calendar = _Calendar(
        events=[
            {
                "id": "national-day",
                "status": "confirmed",
                "summary": "National Day",
                "start": {"date": "2026-12-02"},
                "end": {"date": "2026-12-04"},
            }
        ]
    )
    db = _Db()
    result = sync_uae_holidays(
        "clinic",
        db=db,
        calendar_client=calendar,
        now=datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert {row["holiday_date"] for row in db.state["upserts"]} == {
        "2026-12-02",
        "2026-12-03",
    }
    assert db.state["sync"]["sync_token"] == "next-token"
    assert db.state["sync"]["last_status"] == "success"


def test_sync_deletes_cancelled_event_rows() -> None:
    calendar = _Calendar(events=[{"id": "gone", "status": "cancelled"}])
    db = _Db(
        sync={"calendar_id": UAE_CALENDAR, "sync_token": "token"},
        existing=[
            {"id": "row-1", "google_event_id": "gone", "holiday_date": "2027-01-01"}
        ],
    )
    result = sync_uae_holidays(
        "clinic",
        db=db,
        calendar_client=calendar,
        now=datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "success"
    assert db.state["deleted"] == ["row-1"]


def test_empty_full_sync_preserves_existing_rows() -> None:
    calendar = _Calendar(events=[])
    db = _Db(
        existing=[
            {"id": "row-1", "google_event_id": "holiday", "holiday_date": "2027-01-01"}
        ]
    )
    result = sync_uae_holidays(
        "clinic",
        db=db,
        calendar_client=calendar,
        now=datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "empty"
    assert db.state["deleted"] == []
    assert db.state["sync"]["last_status"] == "empty"


def test_expired_sync_token_falls_back_to_full_sync() -> None:
    calendar = _Calendar(
        events=[
            {
                "id": "new-year",
                "status": "confirmed",
                "summary": "New Year's Day",
                "start": {"date": "2027-01-01"},
                "end": {"date": "2027-01-02"},
            }
        ]
    )
    db = _Db(sync={"calendar_id": UAE_CALENDAR, "sync_token": "expired"})
    result = sync_uae_holidays(
        "clinic",
        db=db,
        calendar_client=calendar,
        now=datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "success"
    assert result["incremental"] is False
    assert "expired" in calendar.calls
    assert any(row["holiday_date"] == "2027-01-01" for row in db.state["upserts"])


class _ExpiredResponse:
    status_code = 410

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class _ExpiredSession:
    def get(self, *_args, **_kwargs):
        return _ExpiredResponse()


def test_calendar_client_raises_on_expired_sync_token() -> None:
    client = GoogleCalendarClient(_ExpiredSession(), timeout_seconds=10)  # type: ignore[arg-type]
    with pytest.raises(SyncTokenExpiredError):
        client.list_events("calendar@example.com", sync_token="expired")
