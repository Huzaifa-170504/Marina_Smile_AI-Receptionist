from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.integrations.google_calendar import GoogleCalendarClient


TIMEZONE = ZoneInfo("Asia/Dubai")


class _Response:
    def __init__(self, payload: dict[str, object] | None = None, status_code: int = 200):
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("request failed")

    def json(self) -> dict[str, object]:
        return self.payload


class _Session:
    def __init__(self) -> None:
        self.posted: dict[str, object] | None = None
        self.deleted_url: str | None = None
        self.put_data: dict[str, object] | None = None

    def get(self, _url: str, **_kwargs: object) -> _Response:
        return _Response(
            {
                "id": "event-123",
                "etag": '"etag-1"',
                "extendedProperties": {
                    "private": {
                        "clinic_id": "clinic-1",
                        "dentist_id": "dentist-1",
                        "appointment_id": "appointment-1",
                        "service_id": "service-1",
                        "contact_id": "contact-1",
                    }
                },
            }
        )

    def post(self, _url: str, **kwargs: object) -> _Response:
        self.posted = kwargs
        return _Response({"id": "event-123"})

    def delete(self, url: str, **_kwargs: object) -> _Response:
        self.deleted_url = url
        return _Response(status_code=204)

    def put(self, _url: str, **kwargs: object) -> _Response:
        self.put_data = kwargs
        return _Response({"id": "event-123"})


def test_calendar_event_contains_only_operational_content_and_metadata() -> None:
    session = _Session()
    client = GoogleCalendarClient(session, timeout_seconds=10)  # type: ignore[arg-type]
    start = datetime(2026, 10, 14, 16, 30, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 14, 17, 30, tzinfo=TIMEZONE)

    event_id = client.create_appointment_event(
        calendar_id="calendar@example.com",
        clinic_id="clinic-1",
        clinic_name="Marina Smile",
        dentist_id="dentist-1",
        appointment_id="appointment-1",
        contact_id="contact-1",
        service_id="service-1",
        service_name="Teeth Whitening",
        start_at=start,
        end_at=end,
        timezone_name="Asia/Dubai",
    )

    assert event_id == "event-123"
    assert session.posted is not None
    body = session.posted["json"]
    assert isinstance(body, dict)
    assert body["summary"] == "Marina Smile - Teeth Whitening"
    assert "description" not in body
    assert body["start"] == {
        "dateTime": start.isoformat(),
        "timeZone": "Asia/Dubai",
    }
    assert body["end"] == {
        "dateTime": end.isoformat(),
        "timeZone": "Asia/Dubai",
    }
    assert body["extendedProperties"] == {
        "private": {
            "clinic_id": "clinic-1",
            "dentist_id": "dentist-1",
            "appointment_id": "appointment-1",
            "service_id": "service-1",
            "contact_id": "contact-1",
        }
    }


def test_calendar_event_deletion_uses_existing_client() -> None:
    session = _Session()
    client = GoogleCalendarClient(session, timeout_seconds=10)  # type: ignore[arg-type]

    client.delete_appointment_event(
        calendar_id="calendar@example.com",
        event_id="event/123",
    )

    assert session.deleted_url is not None
    assert session.deleted_url.endswith("/events/event%2F123")


def test_calendar_creation_requires_timezone_aware_interval() -> None:
    client = GoogleCalendarClient(_Session(), timeout_seconds=10)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        client.create_appointment_event(
            calendar_id="calendar@example.com",
            clinic_id="clinic-1",
            clinic_name="Marina Smile",
            dentist_id="dentist-1",
            appointment_id="appointment-1",
            contact_id="contact-1",
            service_id="service-1",
            service_name="Teeth Whitening",
            start_at=datetime(2026, 10, 14, 16, 30),
            end_at=datetime(2026, 10, 14, 17, 30),
            timezone_name="Asia/Dubai",
        )


def test_calendar_update_preserves_required_private_metadata_and_uses_etag() -> None:
    session = _Session()
    client = GoogleCalendarClient(session, timeout_seconds=10)  # type: ignore[arg-type]
    start = datetime(2026, 10, 15, 18, 0, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 15, 19, 0, tzinfo=TIMEZONE)

    client.update_appointment_event(
        calendar_id="calendar@example.com",
        event_id="event-123",
        clinic_id="clinic-1",
        clinic_name="Marina Smile",
        dentist_id="dentist-2",
        appointment_id="appointment-1",
        contact_id="contact-1",
        service_id="service-1",
        service_name="Teeth Whitening",
        start_at=start,
        end_at=end,
        timezone_name="Asia/Dubai",
    )

    assert session.put_data is not None
    assert session.put_data["headers"] == {"If-Match": '"etag-1"'}
    body = session.put_data["json"]
    assert isinstance(body, dict)
    assert body["extendedProperties"] == {
        "private": {
            "clinic_id": "clinic-1",
            "appointment_id": "appointment-1",
            "contact_id": "contact-1",
            "service_id": "service-1",
            "dentist_id": "dentist-2",
        }
    }


def test_calendar_delete_treats_missing_event_as_idempotent() -> None:
    class MissingSession(_Session):
        def delete(self, url: str, **_kwargs: object) -> _Response:
            self.deleted_url = url
            return _Response(status_code=410)

    client = GoogleCalendarClient(MissingSession(), timeout_seconds=10)  # type: ignore[arg-type]

    client.delete_appointment_event(
        calendar_id="calendar@example.com",
        event_id="already-deleted",
    )


def test_calendar_busy_query_excludes_current_event_id() -> None:
    class EventsSession(_Session):
        def get(self, _url: str, **_kwargs: object) -> _Response:
            return _Response(
                {
                    "items": [
                        {
                            "id": "current-event",
                            "status": "confirmed",
                            "start": {"dateTime": "2026-10-14T16:30:00+04:00"},
                            "end": {"dateTime": "2026-10-14T17:30:00+04:00"},
                            "extendedProperties": {
                                "private": {
                                    "clinic_id": "clinic-1",
                                    "dentist_id": "dentist-1",
                                    "appointment_id": "appointment-1",
                                }
                            },
                        }
                    ]
                }
            )

    client = GoogleCalendarClient(EventsSession(), timeout_seconds=10)  # type: ignore[arg-type]

    intervals = client.list_busy_intervals(
        calendar_id="calendar@example.com",
        clinic_id="clinic-1",
        dentist_id="dentist-1",
        window_start=datetime(2026, 10, 14, 16, 0, tzinfo=TIMEZONE),
        window_end=datetime(2026, 10, 14, 18, 0, tzinfo=TIMEZONE),
        exclude_event_id="current-event",
    )

    assert intervals == []
