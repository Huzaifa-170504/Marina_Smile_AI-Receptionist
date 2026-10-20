from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.operations import booking
from app.operations.booking_models import (
    AppointmentRecord,
    BookingStatus,
    Contact,
)
from app.operations.booking_response import format_booking_response
from app.operations.models import BusyInterval, Clinic, Dentist, DentistSchedule, Service
from app.repositories.appointments import AppointmentConflictError


TIMEZONE = ZoneInfo("Asia/Dubai")
CLINIC_ID = "10000000-0000-4000-8000-000000000001"
CONTACT_ID = "20000000-0000-4000-8000-000000000001"
DENTIST_ID = "30000000-0000-4000-8000-000000000001"
SERVICE_ID = "40000000-0000-4000-8000-000000000001"
APPOINTMENT_ID = "50000000-0000-4000-8000-000000000001"
START = datetime(2026, 10, 14, 16, 30, tzinfo=TIMEZONE)
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=TIMEZONE)


class _Calendar:
    def __init__(
        self,
        *,
        busy: list[BusyInterval] | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.busy = busy or []
        self.create_error = create_error
        self.created: list[dict[str, object]] = []
        self.deleted: list[str] = []

    def list_busy_intervals(self, **_kwargs: object) -> list[BusyInterval]:
        return list(self.busy)

    def create_appointment_event(self, **kwargs: object) -> str:
        if self.create_error:
            raise self.create_error
        self.created.append(kwargs)
        return "calendar-event-1"

    def delete_appointment_event(self, **kwargs: object) -> None:
        self.deleted.append(str(kwargs["event_id"]))


def _configure_booking(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    clinic = Clinic(
        id=CLINIC_ID,
        name="Marina Smile",
        timezone="Asia/Dubai",
        google_calendar_id="calendar@example.com",
    )
    contact = Contact(id=CONTACT_ID, clinic_id=CLINIC_ID, name="Test Patient")
    dentist = Dentist(id=DENTIST_ID, clinic_id=CLINIC_ID, name="Dr. Sara Haddad")
    service = Service(
        id=SERVICE_ID,
        clinic_id=CLINIC_ID,
        name="In-clinic teeth whitening",
        duration_minutes=60,
    )
    appointment = AppointmentRecord(
        id=APPOINTMENT_ID,
        clinic_id=CLINIC_ID,
        contact_id=CONTACT_ID,
        dentist_id=DENTIST_ID,
        service_id=SERVICE_ID,
        start_at=START,
        end_at=START.replace(hour=17, minute=30),
        status="pending",
        booking_request_id="60000000-0000-4000-8000-000000000001",
    )
    state: dict[str, object] = {
        "clinic": clinic,
        "contact": contact,
        "dentist": dentist,
        "service": service,
        "appointment": appointment,
        "cancelled": [],
        "confirmed": [],
    }
    monkeypatch.setattr(
        booking,
        "get_settings",
        lambda: SimpleNamespace(
            availability_slot_interval_minutes=15,
            google_calendar_timeout_seconds=10,
            google_application_credentials=None,
        ),
    )
    monkeypatch.setattr(booking, "get_active_clinic", lambda *_args, **_kwargs: clinic)
    monkeypatch.setattr(booking, "get_contact", lambda *_args, **_kwargs: contact)
    monkeypatch.setattr(
        booking,
        "list_active_dentists",
        lambda *_args, **_kwargs: [dentist],
    )
    monkeypatch.setattr(
        booking,
        "list_active_services",
        lambda *_args, **_kwargs: [service],
    )
    monkeypatch.setattr(
        booking,
        "filter_dentists_for_service",
        lambda *_args, **_kwargs: [dentist],
    )
    monkeypatch.setattr(
        booking,
        "list_active_schedules",
        lambda *_args, **_kwargs: [
            DentistSchedule(
                dentist_id=DENTIST_ID,
                weekday=START.weekday(),
                start_time=time(9, 0),
                end_time=time(18, 0),
            )
        ],
    )
    monkeypatch.setattr(
        booking,
        "list_blocking_appointments",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        booking,
        "get_appointment_by_booking_request",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        booking,
        "create_pending_appointment",
        lambda **_kwargs: appointment,
    )

    def confirm(*_args: object, **_kwargs: object) -> AppointmentRecord:
        cast_confirmed = state["confirmed"]
        assert isinstance(cast_confirmed, list)
        cast_confirmed.append(APPOINTMENT_ID)
        return appointment.model_copy(
            update={
                "status": "confirmed",
                "google_calendar_event_id": "calendar-event-1",
            }
        )

    def cancel(*_args: object, **_kwargs: object) -> AppointmentRecord:
        cast_cancelled = state["cancelled"]
        assert isinstance(cast_cancelled, list)
        cast_cancelled.append(APPOINTMENT_ID)
        return appointment.model_copy(update={"status": "cancelled"})

    monkeypatch.setattr(booking, "confirm_appointment", confirm)
    monkeypatch.setattr(booking, "cancel_pending_appointment", cancel)
    return state


@pytest.fixture
def configured_booking(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return _configure_booking(monkeypatch)


def _book(calendar: _Calendar, **overrides: object):
    arguments = {
        "clinic_id": CLINIC_ID,
        "contact_id": CONTACT_ID,
        "dentist_id": DENTIST_ID,
        "service_id": SERVICE_ID,
        "start_at": START,
        "booking_request_id": "60000000-0000-4000-8000-000000000001",
        "now": NOW,
        "client": SimpleNamespace(),
        "calendar_client": calendar,
    }
    arguments.update(overrides)
    return booking.book_appointment(**arguments)  # type: ignore[arg-type]


def test_successful_calendar_creation_confirms_database_appointment(
    configured_booking: dict[str, object],
) -> None:
    calendar = _Calendar()

    result = _book(calendar)

    assert result.status == BookingStatus.CONFIRMED
    assert result.appointment_id == APPOINTMENT_ID
    assert result.calendar_event_id == "calendar-event-1"
    assert configured_booking["confirmed"] == [APPOINTMENT_ID]
    assert len(calendar.created) == 1
    assert "tomorrow at 4:30 PM" in format_booking_response(
        result,
        timezone_name="Asia/Dubai",
        now=NOW,
    )


def test_missing_service_is_rejected_before_booking() -> None:
    result = booking.book_appointment(
        CLINIC_ID,
        CONTACT_ID,
        DENTIST_ID,
        None,
        START,
    )

    assert result.status == BookingStatus.NEEDS_SERVICE
    assert format_booking_response(result, timezone_name="Asia/Dubai") == (
        "Which treatment would you like to book?"
    )


def test_contact_not_found(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(booking, "get_contact", lambda *_args, **_kwargs: None)

    assert _book(_Calendar()).status == BookingStatus.CONTACT_NOT_FOUND


def test_database_failure_returns_safe_error(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> Contact:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(booking, "get_contact", fail)

    result = _book(_Calendar())

    assert result.status == BookingStatus.ERROR
    assert result.reason == "The booking could not be completed."


def test_dentist_not_found(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(booking, "list_active_dentists", lambda *_args, **_kwargs: [])

    assert _book(_Calendar()).status == BookingStatus.DENTIST_NOT_FOUND


def test_service_not_found(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(booking, "list_active_services", lambda *_args, **_kwargs: [])

    assert _book(_Calendar()).status == BookingStatus.SERVICE_NOT_FOUND


def test_dentist_service_mismatch(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        booking,
        "filter_dentists_for_service",
        lambda *_args, **_kwargs: [],
    )

    assert _book(_Calendar()).status == BookingStatus.DENTIST_SERVICE_MISMATCH


def test_outside_working_hours(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        booking,
        "list_active_schedules",
        lambda *_args, **_kwargs: [],
    )

    assert _book(_Calendar()).status == BookingStatus.OUTSIDE_WORKING_HOURS


def test_existing_database_conflict_returns_slot_taken(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        booking,
        "list_blocking_appointments",
        lambda *_args, **_kwargs: [
            BusyInterval(
                dentist_id=DENTIST_ID,
                start_at=START,
                end_at=START.replace(hour=17, minute=30),
            )
        ],
    )

    assert _book(_Calendar()).status == BookingStatus.SLOT_TAKEN


def test_atomic_insert_conflict_returns_slot_taken(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def conflict(**_kwargs: object) -> AppointmentRecord:
        raise AppointmentConflictError("overlap")

    monkeypatch.setattr(booking, "create_pending_appointment", conflict)

    assert _book(_Calendar()).status == BookingStatus.SLOT_TAKEN


def test_calendar_conflict_prevents_pending_insert(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        booking,
        "create_pending_appointment",
        lambda **_kwargs: pytest.fail("pending insert must not run"),
    )
    busy = BusyInterval(
        dentist_id=DENTIST_ID,
        start_at=START,
        end_at=START.replace(hour=17, minute=30),
    )

    assert _book(_Calendar(busy=[busy])).status == BookingStatus.CALENDAR_CONFLICT


def test_calendar_create_failure_cancels_pending_appointment(
    configured_booking: dict[str, object],
) -> None:
    result = _book(_Calendar(create_error=RuntimeError("unavailable")))

    assert result.status == BookingStatus.CALENDAR_ERROR
    assert configured_booking["cancelled"] == [APPOINTMENT_ID]


def test_final_confirmation_failure_deletes_event_and_cancels_pending(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        booking,
        "confirm_appointment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db failure")),
    )
    calendar = _Calendar()

    result = _book(calendar)

    assert result.status == BookingStatus.CONSISTENCY_ERROR
    assert calendar.deleted == ["calendar-event-1"]
    assert configured_booking["cancelled"] == [APPOINTMENT_ID]


def test_duplicate_idempotency_request_returns_existing_without_insert(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = configured_booking["appointment"]
    assert isinstance(appointment, AppointmentRecord)
    existing = appointment.model_copy(
        update={
            "status": "confirmed",
            "google_calendar_event_id": "calendar-event-1",
        }
    )
    monkeypatch.setattr(
        booking,
        "get_appointment_by_booking_request",
        lambda *_args, **_kwargs: existing,
    )
    monkeypatch.setattr(
        booking,
        "create_pending_appointment",
        lambda **_kwargs: pytest.fail("duplicate insert must not run"),
    )

    result = _book(_Calendar())

    assert result.status == BookingStatus.CONFIRMED
    assert result.appointment_id == APPOINTMENT_ID


def test_concurrent_idempotent_conflict_returns_existing_request(
    configured_booking: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = configured_booking["appointment"]
    assert isinstance(appointment, AppointmentRecord)
    lookups = iter([None, appointment])
    monkeypatch.setattr(
        booking,
        "get_appointment_by_booking_request",
        lambda *_args, **_kwargs: next(lookups),
    )

    def conflict(**_kwargs: object) -> AppointmentRecord:
        raise AppointmentConflictError("constraint order is nondeterministic")

    monkeypatch.setattr(booking, "create_pending_appointment", conflict)

    result = _book(_Calendar())

    assert result.status == BookingStatus.IN_PROGRESS
    assert result.appointment_id == APPOINTMENT_ID


def test_invalid_idempotency_key_is_rejected(
    configured_booking: dict[str, object],
) -> None:
    result = _book(_Calendar(), booking_request_id=str(uuid4()) + "x")

    assert result.status == BookingStatus.ERROR
