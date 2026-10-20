from __future__ import annotations

import pytest

from app.operations import appointment_lookup
from app.operations.appointment_change_models import AppointmentLookupStatus
from app.operations.appointment_lifecycle import (
    AppointmentStatus,
    can_transition,
    validate_transition,
)
from app.operations.booking_models import AppointmentRecord
from app.operations.models import Clinic
from tests.test_reschedule import CLINIC_ID, CONTACT_ID, _appointment


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "confirmed"),
        ("pending", "cancelled"),
        ("confirmed", "confirmed"),
        ("confirmed", "cancelled"),
        ("confirmed", "completed"),
        ("confirmed", "no_show"),
    ],
)
def test_allowed_lifecycle_transitions(current: str, target: str) -> None:
    assert can_transition(current, target)
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "pending"),
        ("cancelled", "confirmed"),
        ("completed", "confirmed"),
        ("no_show", "confirmed"),
        ("cancelled", "cancelled"),
        ("unknown", "confirmed"),
    ],
)
def test_disallowed_or_unknown_lifecycle_transitions(
    current: str,
    target: str,
) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ValueError):
        validate_transition(current, target)


def test_status_values_match_database_contract() -> None:
    assert {item.value for item in AppointmentStatus} == {
        "pending",
        "confirmed",
        "cancelled",
        "completed",
        "no_show",
    }


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, AppointmentLookupStatus.APPOINTMENT_NOT_FOUND),
        (1, AppointmentLookupStatus.FOUND),
        (2, AppointmentLookupStatus.NEEDS_CLARIFICATION),
    ],
)
def test_contact_scoped_lookup_never_guesses_between_matches(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    expected: AppointmentLookupStatus,
) -> None:
    records: list[AppointmentRecord] = [
        _appointment(id=f"80000000-0000-4000-8000-{index:012d}")
        for index in range(1, count + 1)
    ]
    monkeypatch.setattr(
        appointment_lookup,
        "get_active_clinic",
        lambda *_args, **_kwargs: Clinic(
            id=CLINIC_ID,
            name="Marina Smile",
            timezone="Asia/Dubai",
        ),
    )
    monkeypatch.setattr(
        appointment_lookup,
        "find_contact_appointments",
        lambda *_args, **_kwargs: records,
    )

    result = appointment_lookup.find_contact_appointment(
        CLINIC_ID,
        CONTACT_ID,
        client=object(),  # type: ignore[arg-type]
    )

    assert result.status == expected
    if count == 1:
        assert result.appointment == records[0]
