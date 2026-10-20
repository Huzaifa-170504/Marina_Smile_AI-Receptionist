from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.operations import reschedule
from app.operations.appointment_change_models import (
    AppointmentActor,
    AppointmentActorType,
    RescheduleStatus,
)
from app.operations.booking_models import AppointmentRecord
from app.operations.models import BusyInterval, Clinic, Dentist, DentistSchedule, Service
from app.repositories.appointments import AppointmentConflictError


TIMEZONE = ZoneInfo("Asia/Dubai")
CLINIC_ID = "10000000-0000-4000-8000-000000000001"
CONTACT_ID = "40000000-0000-4000-8000-000000000001"
OTHER_CONTACT_ID = "40000000-0000-4000-8000-000000000002"
DENTIST_ID = "20000000-0000-4000-8000-000000000001"
OTHER_DENTIST_ID = "20000000-0000-4000-8000-000000000002"
SERVICE_ID = "30000000-0000-4000-8000-000000000003"
APPOINTMENT_ID = "80000000-0000-4000-8000-000000000001"
OLD_START = datetime(2026, 10, 15, 16, 30, tzinfo=TIMEZONE)
OLD_END = datetime(2026, 10, 15, 17, 30, tzinfo=TIMEZONE)
NEW_START = datetime(2026, 10, 16, 18, 0, tzinfo=TIMEZONE)
NEW_END = datetime(2026, 10, 16, 19, 0, tzinfo=TIMEZONE)
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=TIMEZONE)
ACTOR = AppointmentActor(
    actor_type=AppointmentActorType.PATIENT,
    actor_id=CONTACT_ID,
    reason="Patient requested a later time",
)


class _Calendar:
    def __init__(
        self,
        *,
        busy: list[BusyInterval] | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.busy = busy or []
        self.update_error = update_error
        self.list_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []

    def list_busy_intervals(self, **kwargs: object) -> list[BusyInterval]:
        self.list_calls.append(kwargs)
        return self.busy

    def update_appointment_event(self, **kwargs: object) -> None:
        self.update_calls.append(kwargs)
        if self.update_error:
            raise self.update_error

    def create_appointment_event(self, **_kwargs: object) -> str:
        return "unused"

    def delete_appointment_event(self, **_kwargs: object) -> None:
        return None


def _appointment(**updates: object) -> AppointmentRecord:
    base = AppointmentRecord(
        id=APPOINTMENT_ID,
        clinic_id=CLINIC_ID,
        contact_id=CONTACT_ID,
        dentist_id=DENTIST_ID,
        service_id=SERVICE_ID,
        start_at=OLD_START,
        end_at=OLD_END,
        status="confirmed",
        google_calendar_event_id="calendar-event-1",
    )
    return base.model_copy(update=updates)


def _configure_reschedule(
    monkeypatch: pytest.MonkeyPatch,
    *,
    appointment: AppointmentRecord | None = None,
) -> dict[str, object]:
    record = appointment or _appointment()
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(
        reschedule,
        "get_settings",
        lambda: SimpleNamespace(
            availability_slot_interval_minutes=15,
            google_calendar_timeout_seconds=10,
            google_application_credentials=None,
        ),
    )
    monkeypatch.setattr(
        reschedule,
        "get_appointment_for_contact",
        lambda *_args, **_kwargs: record,
    )
    monkeypatch.setattr(
        reschedule,
        "get_active_clinic",
        lambda *_args, **_kwargs: Clinic(
            id=CLINIC_ID,
            name="Marina Smile",
            timezone="Asia/Dubai",
            google_calendar_id="clinic-calendar",
        ),
    )
    dentists = [
        Dentist(id=DENTIST_ID, clinic_id=CLINIC_ID, name="Dr. Sara Haddad"),
        Dentist(id=OTHER_DENTIST_ID, clinic_id=CLINIC_ID, name="Dr. Omar"),
    ]
    monkeypatch.setattr(reschedule, "list_active_dentists", lambda *_a, **_k: dentists)
    monkeypatch.setattr(
        reschedule,
        "list_active_services",
        lambda *_a, **_k: [
            Service(
                id=SERVICE_ID,
                clinic_id=CLINIC_ID,
                name="In-clinic teeth whitening",
                duration_minutes=60,
            )
        ],
    )
    monkeypatch.setattr(
        reschedule,
        "filter_dentists_for_service",
        lambda _clinic, selected, _service, **_kwargs: selected,
    )
    monkeypatch.setattr(
        reschedule,
        "list_active_schedules",
        lambda *_a, **_k: [
            DentistSchedule(
                dentist_id=DENTIST_ID,
                weekday=NEW_START.weekday(),
                start_time=time(9),
                end_time=time(21),
            ),
            DentistSchedule(
                dentist_id=OTHER_DENTIST_ID,
                weekday=NEW_START.weekday(),
                start_time=time(9),
                end_time=time(21),
            ),
        ],
    )
    monkeypatch.setattr(reschedule, "list_blocking_appointments", lambda *_a, **_k: [])

    def update(**kwargs: object) -> AppointmentRecord:
        updates.append(kwargs)
        return record.model_copy(
            update={
                "dentist_id": kwargs["new_dentist_id"],
                "start_at": kwargs["new_start_at"],
                "end_at": kwargs["new_end_at"],
            }
        )

    monkeypatch.setattr(reschedule, "update_appointment_schedule", update)
    return {"appointment": record, "updates": updates}


def _reschedule(calendar: _Calendar, **overrides: object):
    values: dict[str, object] = {
        "actor": ACTOR,
        "now": NOW,
        "client": object(),
        "calendar_client": calendar,
    }
    values.update(overrides)
    return reschedule.reschedule_appointment(
        CLINIC_ID,
        CONTACT_ID,
        APPOINTMENT_ID,
        NEW_START,
        **values,
    )


def test_reschedule_updates_database_then_same_calendar_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_reschedule(monkeypatch)
    calendar = _Calendar()

    result = _reschedule(calendar)

    assert result.status == RescheduleStatus.RESCHEDULED
    assert result.new_start_at == NEW_START
    assert len(configured["updates"]) == 1
    assert calendar.update_calls[0]["event_id"] == "calendar-event-1"
    assert calendar.update_calls[0]["appointment_id"] == APPOINTMENT_ID
    assert calendar.list_calls[0]["exclude_event_id"] == "calendar-event-1"


def test_current_appointment_is_excluded_from_database_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    calls: list[dict[str, object]] = []

    def list_busy(*_args: object, **kwargs: object) -> list[BusyInterval]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(reschedule, "list_blocking_appointments", list_busy)

    assert _reschedule(_Calendar()).status == RescheduleStatus.RESCHEDULED
    assert calls[0]["exclude_appointment_id"] == APPOINTMENT_ID


def test_slot_taken_after_precheck_is_reported_by_atomic_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    monkeypatch.setattr(
        reschedule,
        "update_appointment_schedule",
        lambda **_kwargs: (_ for _ in ()).throw(AppointmentConflictError("overlap")),
    )

    result = _reschedule(_Calendar())

    assert result.status == RescheduleStatus.SLOT_TAKEN


def test_calendar_update_failure_restores_original_database_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_reschedule(monkeypatch)

    result = _reschedule(_Calendar(update_error=RuntimeError("calendar down")))

    assert result.status == RescheduleStatus.CALENDAR_ERROR
    assert result.compensation_succeeded is True
    updates = configured["updates"]
    assert len(updates) == 2
    assert updates[1]["new_dentist_id"] == DENTIST_ID
    assert updates[1]["new_start_at"] == OLD_START
    assert updates[1]["new_end_at"] == OLD_END


@pytest.mark.parametrize("status", ["pending", "cancelled", "completed", "no_show"])
def test_non_confirmed_appointments_are_not_reschedulable(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    _configure_reschedule(monkeypatch, appointment=_appointment(status=status))

    assert _reschedule(_Calendar()).status == RescheduleStatus.NOT_RESCHEDULABLE


def test_wrong_contact_or_clinic_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_reschedule(monkeypatch)
    monkeypatch.setattr(
        reschedule,
        "get_appointment_for_contact",
        lambda *_args, **_kwargs: None,
    )

    result = reschedule.reschedule_appointment(
        CLINIC_ID,
        OTHER_CONTACT_ID,
        APPOINTMENT_ID,
        NEW_START,
        actor=ACTOR,
        now=NOW,
        client=object(),  # type: ignore[arg-type]
        calendar_client=_Calendar(),
    )

    assert result.status == RescheduleStatus.APPOINTMENT_NOT_FOUND


def test_past_reschedule_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_reschedule(monkeypatch)

    result = reschedule.reschedule_appointment(
        CLINIC_ID,
        CONTACT_ID,
        APPOINTMENT_ID,
        OLD_START,
        actor=ACTOR,
        now=OLD_END,
        client=object(),  # type: ignore[arg-type]
        calendar_client=_Calendar(),
    )

    assert result.status == RescheduleStatus.INVALID_TIME


def test_new_dentist_must_support_existing_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    monkeypatch.setattr(
        reschedule,
        "filter_dentists_for_service",
        lambda *_args, **_kwargs: [],
    )

    result = _reschedule(_Calendar(), new_dentist_id=OTHER_DENTIST_ID)

    assert result.status == RescheduleStatus.DENTIST_SERVICE_MISMATCH


def test_reschedule_cannot_change_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_reschedule(monkeypatch)

    result = _reschedule(
        _Calendar(),
        requested_service_id="30000000-0000-4000-8000-000000000004",
    )

    assert result.status == RescheduleStatus.CHANGE_SERVICE_UNSUPPORTED


def test_actor_context_is_preserved_without_bypassing_contact_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    actor = AppointmentActor(
        actor_type=AppointmentActorType.RECEPTIONIST,
        actor_id="staff-42",
        reason="Patient called the clinic",
    )

    result = _reschedule(_Calendar(), actor=actor)

    assert result.status == RescheduleStatus.RESCHEDULED
    assert result.actor == actor
