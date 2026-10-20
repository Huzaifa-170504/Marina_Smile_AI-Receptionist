from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError

from app.operations.availability import intervals_overlap
from app.operations.booking_models import AppointmentRecord, BookingStatus
from app.repositories.appointments import (
    AppointmentConflictError,
    AppointmentIdempotencyError,
    create_pending_appointment,
)
from tests.test_booking import (
    APPOINTMENT_ID,
    CLINIC_ID,
    CONTACT_ID,
    DENTIST_ID,
    NOW,
    SERVICE_ID,
    START,
    _Calendar,
    _book,
    _configure_booking,
)


TIMEZONE = ZoneInfo("Asia/Dubai")


def test_two_competing_users_cannot_both_reserve_same_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.operations import booking

    configured_booking = _configure_booking(monkeypatch)
    barrier = Barrier(2)
    outcomes = deque(["reserved", "conflict"])
    appointment = configured_booking["appointment"]
    assert isinstance(appointment, AppointmentRecord)

    def atomic_insert(**_kwargs: object) -> AppointmentRecord:
        barrier.wait(timeout=2)
        if outcomes.popleft() == "conflict":
            raise AppointmentConflictError("database exclusion violation")
        return appointment

    monkeypatch.setattr(booking, "create_pending_appointment", atomic_insert)

    request_ids = [
        "60000000-0000-4000-8000-000000000001",
        "60000000-0000-4000-8000-000000000002",
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda request_id: _book(
                    _Calendar(),
                    booking_request_id=request_id,
                ),
                request_ids,
            )
        )

    assert sorted(result.status.value for result in results) == [
        BookingStatus.CONFIRMED.value,
        BookingStatus.SLOT_TAKEN.value,
    ]


@pytest.mark.parametrize(
    ("new_start", "new_end", "overlaps"),
    [
        (datetime(2026, 10, 14, 18, 30, tzinfo=TIMEZONE), datetime(2026, 10, 14, 19, 30, tzinfo=TIMEZONE), True),
        (datetime(2026, 10, 14, 17, 30, tzinfo=TIMEZONE), datetime(2026, 10, 14, 18, 15, tzinfo=TIMEZONE), True),
        (datetime(2026, 10, 14, 17, 0, tzinfo=TIMEZONE), datetime(2026, 10, 14, 18, 0, tzinfo=TIMEZONE), False),
        (datetime(2026, 10, 14, 19, 0, tzinfo=TIMEZONE), datetime(2026, 10, 14, 20, 0, tzinfo=TIMEZONE), False),
    ],
)
def test_half_open_overlap_semantics(
    new_start: datetime,
    new_end: datetime,
    overlaps: bool,
) -> None:
    existing_start = datetime(2026, 10, 14, 18, 0, tzinfo=TIMEZONE)
    existing_end = datetime(2026, 10, 14, 19, 0, tzinfo=TIMEZONE)

    assert intervals_overlap(
        existing_start,
        existing_end,
        new_start,
        new_end,
    ) is overlaps


def test_different_dentists_do_not_share_exclusion_key() -> None:
    migration = Path("supabase/migrations/0006_appointments_and_overlap.sql").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(migration.split())

    assert "dentist_id with =" in normalized
    assert "tstzrange(start_at, end_at, '[)') with &&" in normalized
    assert "where (status in ('pending', 'confirmed'))" in normalized


class _FailingInsert:
    def __init__(self, error: APIError) -> None:
        self.error = error

    def table(self, _name: str) -> "_FailingInsert":
        return self

    def insert(self, _payload: dict[str, object]) -> "_FailingInsert":
        return self

    def execute(self) -> None:
        raise self.error


def test_repository_translates_exclusion_violation() -> None:
    client = _FailingInsert(
        APIError(
            {
                "code": "23P01",
                "message": "conflicting key value violates exclusion constraint",
            }
        )
    )

    with pytest.raises(AppointmentConflictError):
        create_pending_appointment(
            clinic_id=CLINIC_ID,
            contact_id=CONTACT_ID,
            dentist_id=DENTIST_ID,
            service_id=SERVICE_ID,
            start_at=START,
            end_at=START.replace(hour=17, minute=30),
            client=client,  # type: ignore[arg-type]
        )


def test_repository_translates_idempotency_violation() -> None:
    client = _FailingInsert(
        APIError(
            {
                "code": "23505",
                "message": "duplicate key violates appointments_booking_request_id_key",
                "details": "Key (booking_request_id) already exists",
            }
        )
    )

    with pytest.raises(AppointmentIdempotencyError):
        create_pending_appointment(
            clinic_id=CLINIC_ID,
            contact_id=CONTACT_ID,
            dentist_id=DENTIST_ID,
            service_id=SERVICE_ID,
            start_at=START,
            end_at=START.replace(hour=17, minute=30),
            booking_request_id="60000000-0000-4000-8000-000000000001",
            client=client,  # type: ignore[arg-type]
        )
