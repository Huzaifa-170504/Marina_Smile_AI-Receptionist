from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.operations import cancellation
from app.operations.appointment_change_models import (
    AppointmentActor,
    AppointmentActorType,
    CancellationStatus,
)
from app.operations.booking_models import AppointmentRecord
from app.operations.models import Clinic, Dentist, Service
from app.repositories.appointments import AppointmentStateError
from tests.test_reschedule import (
    APPOINTMENT_ID,
    CLINIC_ID,
    CONTACT_ID,
    DENTIST_ID,
    OTHER_CONTACT_ID,
    SERVICE_ID,
    _appointment,
)


ACTOR = AppointmentActor(
    actor_type=AppointmentActorType.PATIENT,
    actor_id=CONTACT_ID,
    reason="Patient cannot attend",
)


class _Calendar:
    def __init__(
        self,
        *,
        delete_error: Exception | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.delete_error = delete_error
        self.create_error = create_error
        self.deleted: list[str] = []
        self.created: list[dict[str, object]] = []

    def delete_appointment_event(self, **kwargs: object) -> None:
        self.deleted.append(str(kwargs["event_id"]))
        if self.delete_error:
            raise self.delete_error

    def create_appointment_event(self, **kwargs: object) -> str:
        self.created.append(kwargs)
        if self.create_error:
            raise self.create_error
        return "restored-calendar-event"

    def list_busy_intervals(self, **_kwargs: object) -> list[object]:
        return []

    def update_appointment_event(self, **_kwargs: object) -> None:
        return None


def _configure_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    appointment: AppointmentRecord | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {"appointment": appointment or _appointment()}
    calls: list[str] = []
    event_ids: list[str] = []

    monkeypatch.setattr(
        cancellation,
        "get_settings",
        lambda: SimpleNamespace(
            google_calendar_timeout_seconds=10,
            google_application_credentials=None,
        ),
    )
    monkeypatch.setattr(
        cancellation,
        "get_appointment_for_contact",
        lambda *_args, **_kwargs: state["appointment"],
    )
    monkeypatch.setattr(
        cancellation,
        "get_active_clinic",
        lambda *_args, **_kwargs: Clinic(
            id=CLINIC_ID,
            name="Marina Smile",
            timezone="Asia/Dubai",
            google_calendar_id="clinic-calendar",
        ),
    )
    monkeypatch.setattr(
        cancellation,
        "list_active_dentists",
        lambda *_args, **_kwargs: [
            Dentist(id=DENTIST_ID, clinic_id=CLINIC_ID, name="Dr. Sara Haddad")
        ],
    )
    monkeypatch.setattr(
        cancellation,
        "list_active_services",
        lambda *_args, **_kwargs: [
            Service(
                id=SERVICE_ID,
                clinic_id=CLINIC_ID,
                name="In-clinic teeth whitening",
                duration_minutes=60,
            )
        ],
    )

    def cancel(**_kwargs: object) -> AppointmentRecord:
        calls.append("cancel")
        current = state["appointment"]
        assert isinstance(current, AppointmentRecord)
        updated = current.model_copy(update={"status": "cancelled"})
        state["appointment"] = updated
        return updated

    def update_event_id(**kwargs: object) -> AppointmentRecord:
        event_ids.append(str(kwargs["calendar_event_id"]))
        current = state["appointment"]
        assert isinstance(current, AppointmentRecord)
        updated = current.model_copy(
            update={"google_calendar_event_id": kwargs["calendar_event_id"]}
        )
        state["appointment"] = updated
        return updated

    monkeypatch.setattr(cancellation, "cancel_appointment_record", cancel)
    monkeypatch.setattr(
        cancellation,
        "update_appointment_calendar_event_id",
        update_event_id,
    )
    return {"state": state, "calls": calls, "event_ids": event_ids}


def _cancel(calendar: _Calendar):
    return cancellation.cancel_appointment(
        CLINIC_ID,
        CONTACT_ID,
        APPOINTMENT_ID,
        actor=ACTOR,
        client=object(),  # type: ignore[arg-type]
        calendar_client=calendar,
    )


def test_cancellation_deletes_calendar_then_marks_database_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_cancellation(monkeypatch)
    calendar = _Calendar()

    result = _cancel(calendar)

    assert result.status == CancellationStatus.CANCELLED
    assert calendar.deleted == ["calendar-event-1"]
    assert configured["calls"] == ["cancel"]
    current = configured["state"]["appointment"]
    assert isinstance(current, AppointmentRecord)
    assert current.status == "cancelled"


def test_repeated_cancellation_is_idempotent_without_second_calendar_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cancellation(monkeypatch)
    calendar = _Calendar()

    first = _cancel(calendar)
    second = _cancel(calendar)

    assert first.status == CancellationStatus.CANCELLED
    assert second.status == CancellationStatus.ALREADY_CANCELLED
    assert calendar.deleted == ["calendar-event-1"]


def test_cancellation_without_calendar_event_releases_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_cancellation(
        monkeypatch,
        appointment=_appointment(google_calendar_event_id=None),
    )
    calendar = _Calendar()

    result = _cancel(calendar)

    assert result.status == CancellationStatus.CANCELLED
    assert calendar.deleted == []
    current = configured["state"]["appointment"]
    assert isinstance(current, AppointmentRecord)
    assert current.status not in {"pending", "confirmed"}


def test_calendar_delete_failure_leaves_database_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_cancellation(monkeypatch)

    result = _cancel(_Calendar(delete_error=RuntimeError("calendar down")))

    assert result.status == CancellationStatus.CALENDAR_ERROR
    assert configured["calls"] == []


def test_database_failure_recreates_calendar_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_cancellation(monkeypatch)
    monkeypatch.setattr(
        cancellation,
        "cancel_appointment_record",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("database down")),
    )
    calendar = _Calendar()

    result = _cancel(calendar)

    assert result.status == CancellationStatus.ERROR
    assert result.compensation_succeeded is True
    assert calendar.created[0]["appointment_id"] == APPOINTMENT_ID
    assert calendar.created[0]["dentist_id"] == DENTIST_ID
    assert calendar.created[0]["service_id"] == SERVICE_ID
    assert configured["event_ids"] == ["restored-calendar-event"]


def test_database_failure_and_calendar_restore_failure_is_consistency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cancellation(monkeypatch)
    monkeypatch.setattr(
        cancellation,
        "cancel_appointment_record",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("database down")),
    )

    result = _cancel(_Calendar(create_error=RuntimeError("calendar down")))

    assert result.status == CancellationStatus.CONSISTENCY_ERROR
    assert result.compensation_succeeded is False


@pytest.mark.parametrize("status", ["completed", "no_show"])
def test_terminal_appointment_cannot_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    _configure_cancellation(monkeypatch, appointment=_appointment(status=status))

    assert _cancel(_Calendar()).status == CancellationStatus.NOT_CANCELLABLE


def test_already_cancelled_is_successful_idempotent_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cancellation(monkeypatch, appointment=_appointment(status="cancelled"))

    assert _cancel(_Calendar()).status == CancellationStatus.ALREADY_CANCELLED


def test_wrong_contact_or_clinic_cannot_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_cancellation(monkeypatch)
    monkeypatch.setattr(
        cancellation,
        "get_appointment_for_contact",
        lambda *_args, **_kwargs: None,
    )

    result = cancellation.cancel_appointment(
        CLINIC_ID,
        OTHER_CONTACT_ID,
        APPOINTMENT_ID,
        actor=ACTOR,
        client=object(),  # type: ignore[arg-type]
        calendar_client=_Calendar(),
    )

    assert result.status == CancellationStatus.APPOINTMENT_NOT_FOUND


def test_concurrent_status_change_to_cancelled_remains_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _configure_cancellation(monkeypatch)

    def concurrent_cancel(**_kwargs: object) -> AppointmentRecord:
        state = configured["state"]
        current = state["appointment"]
        assert isinstance(current, AppointmentRecord)
        state["appointment"] = current.model_copy(update={"status": "cancelled"})
        raise AppointmentStateError("changed concurrently")

    monkeypatch.setattr(cancellation, "cancel_appointment_record", concurrent_cancel)

    result = _cancel(_Calendar())

    assert result.status == CancellationStatus.ALREADY_CANCELLED
