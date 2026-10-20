from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from postgrest.exceptions import APIError

from app.operations import reschedule
from app.operations.appointment_change_models import RescheduleStatus
from app.operations.availability import intervals_overlap
from app.repositories.appointments import (
    AppointmentConflictError,
    update_appointment_schedule,
)
from tests.test_reschedule import (
    ACTOR,
    APPOINTMENT_ID,
    CLINIC_ID,
    CONTACT_ID,
    DENTIST_ID,
    NEW_END,
    NEW_START,
    NOW,
    OLD_END,
    OLD_START,
    _Calendar,
    _appointment,
    _configure_reschedule,
)


def test_two_reschedules_cannot_claim_same_dentist_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    barrier = Barrier(2)
    lock = Lock()
    outcomes = deque(["moved", "conflict"])

    def atomic_update(**kwargs: object):
        barrier.wait(timeout=2)
        with lock:
            outcome = outcomes.popleft()
        if outcome == "conflict":
            raise AppointmentConflictError("database exclusion violation")
        return _appointment().model_copy(
            update={
                "dentist_id": kwargs["new_dentist_id"],
                "start_at": kwargs["new_start_at"],
                "end_at": kwargs["new_end_at"],
            }
        )

    monkeypatch.setattr(reschedule, "update_appointment_schedule", atomic_update)

    def run() -> RescheduleStatus:
        result = reschedule.reschedule_appointment(
            CLINIC_ID,
            CONTACT_ID,
            APPOINTMENT_ID,
            NEW_START,
            actor=ACTOR,
            now=NOW,
            client=object(),  # type: ignore[arg-type]
            calendar_client=_Calendar(),
        )
        return result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _value: run(), range(2)))

    assert sorted(item.value for item in statuses) == [
        RescheduleStatus.RESCHEDULED.value,
        RescheduleStatus.SLOT_TAKEN.value,
    ]


def test_rollback_conflict_after_calendar_failure_is_consistency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_reschedule(monkeypatch)
    calls = 0

    def update(**kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _appointment().model_copy(
                update={
                    "dentist_id": kwargs["new_dentist_id"],
                    "start_at": kwargs["new_start_at"],
                    "end_at": kwargs["new_end_at"],
                }
            )
        raise AppointmentConflictError(
            "another appointment took the original interval"
        )

    monkeypatch.setattr(reschedule, "update_appointment_schedule", update)

    result = reschedule.reschedule_appointment(
        CLINIC_ID,
        CONTACT_ID,
        APPOINTMENT_ID,
        NEW_START,
        actor=ACTOR,
        now=NOW,
        client=object(),  # type: ignore[arg-type]
        calendar_client=_Calendar(update_error=RuntimeError("calendar update failed")),
    )

    assert calls == 2
    assert result.status == RescheduleStatus.CONSISTENCY_ERROR
    assert result.compensation_succeeded is False
    assert "restored" not in (result.reason or "").casefold()


def test_adjacent_reschedule_intervals_remain_valid() -> None:
    assert not intervals_overlap(OLD_START, OLD_END, OLD_END, NEW_END)
    assert not intervals_overlap(NEW_START, NEW_END, OLD_START, NEW_START)


def test_reschedule_module_has_no_process_local_reservation_state() -> None:
    forbidden_names = {"lock", "mutex", "reservation", "in_progress_requests"}
    module_names = {name.casefold() for name in vars(reschedule)}
    assert forbidden_names.isdisjoint(module_names)
    assert DENTIST_ID != ""


class _ConflictingUpdate:
    def table(self, name: str) -> "_ConflictingUpdate":
        assert name == "appointments"
        return self

    def update(self, _payload: dict[str, object]) -> "_ConflictingUpdate":
        return self

    def eq(self, _column: str, _value: object) -> "_ConflictingUpdate":
        return self

    def execute(self) -> None:
        raise APIError(
            {
                "code": "23P01",
                "message": "conflicting key violates exclusion constraint",
            }
        )


def test_repository_translates_reschedule_exclusion_violation() -> None:
    with pytest.raises(AppointmentConflictError):
        update_appointment_schedule(
            clinic_id=CLINIC_ID,
            contact_id=CONTACT_ID,
            appointment_id=APPOINTMENT_ID,
            expected_status="confirmed",
            expected_dentist_id=DENTIST_ID,
            expected_start_at=OLD_START,
            expected_end_at=OLD_END,
            new_dentist_id=DENTIST_ID,
            new_start_at=NEW_START,
            new_end_at=NEW_END,
            client=_ConflictingUpdate(),  # type: ignore[arg-type]
        )
