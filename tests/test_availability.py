from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.operations.availability import (
    generate_candidate_starts,
    intervals_overlap,
    remove_conflicting_starts,
)
from app.operations.models import BusyInterval, DentistSchedule
from app.repositories.appointments import list_blocking_appointments


TIMEZONE = ZoneInfo("Asia/Dubai")
DENTIST_ID = "20000000-0000-4000-8000-000000000001"
CLINIC_ID = "10000000-0000-4000-8000-000000000001"


def test_overlap_rule_handles_partial_and_touching_intervals() -> None:
    start = datetime(2026, 10, 19, 10, 0, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 19, 10, 45, tzinfo=TIMEZONE)

    assert intervals_overlap(start, end, start.replace(minute=30), end.replace(hour=11))
    assert not intervals_overlap(start, end, end, end.replace(hour=11))


def test_candidate_slots_align_and_fit_full_duration() -> None:
    starts = generate_candidate_starts(
        [
            DentistSchedule(
                dentist_id=DENTIST_ID,
                weekday=0,
                start_time=time(9, 0),
                end_time=time(10, 30),
            )
        ],
        window_start=datetime(2026, 10, 19, 9, 7, tzinfo=TIMEZONE),
        window_end=datetime(2026, 10, 19, 10, 30, tzinfo=TIMEZONE),
        duration_minutes=45,
        interval_minutes=15,
        timezone=TIMEZONE,
    )

    assert starts == [
        datetime(2026, 10, 19, 9, 15, tzinfo=TIMEZONE),
        datetime(2026, 10, 19, 9, 30, tzinfo=TIMEZONE),
        datetime(2026, 10, 19, 9, 45, tzinfo=TIMEZONE),
    ]


def test_partial_busy_interval_removes_candidate() -> None:
    starts = [
        datetime(2026, 10, 19, 9, 0, tzinfo=TIMEZONE),
        datetime(2026, 10, 19, 9, 15, tzinfo=TIMEZONE),
    ]
    busy = BusyInterval(
        dentist_id=DENTIST_ID,
        start_at=datetime(2026, 10, 19, 9, 30, tzinfo=TIMEZONE),
        end_at=datetime(2026, 10, 19, 10, 0, tzinfo=TIMEZONE),
    )

    assert remove_conflicting_starts(
        starts,
        duration_minutes=45,
        busy_intervals=[busy],
    ) == []


class _FakeQuery:
    def __init__(self) -> None:
        self.filters: list[tuple[str, str, object]] = []

    def select(self, _columns: str) -> "_FakeQuery":
        return self

    def eq(self, column: str, value: object) -> "_FakeQuery":
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column: str, value: object) -> "_FakeQuery":
        self.filters.append(("in", column, value))
        return self

    def lt(self, column: str, value: object) -> "_FakeQuery":
        self.filters.append(("lt", column, value))
        return self

    def gt(self, column: str, value: object) -> "_FakeQuery":
        self.filters.append(("gt", column, value))
        return self

    def neq(self, column: str, value: object) -> "_FakeQuery":
        self.filters.append(("neq", column, value))
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _FakeClient:
    def __init__(self) -> None:
        self.query = _FakeQuery()

    def table(self, name: str) -> _FakeQuery:
        assert name == "appointments"
        return self.query


def test_appointment_query_is_tenant_scoped_and_uses_exact_overlap_rule() -> None:
    client = _FakeClient()
    start = datetime(2026, 10, 19, 9, 0, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 19, 12, 0, tzinfo=TIMEZONE)

    list_blocking_appointments(
        CLINIC_ID,
        [DENTIST_ID],
        start,
        end,
        client=client,  # type: ignore[arg-type]
    )

    assert ("eq", "clinic_id", CLINIC_ID) in client.query.filters
    assert ("in", "status", ["pending", "confirmed"]) in client.query.filters
    assert ("lt", "start_at", end.isoformat()) in client.query.filters
    assert ("gt", "end_at", start.isoformat()) in client.query.filters


def test_appointment_conflict_query_can_exclude_current_appointment() -> None:
    client = _FakeClient()
    start = datetime(2026, 10, 19, 9, 0, tzinfo=TIMEZONE)
    end = datetime(2026, 10, 19, 12, 0, tzinfo=TIMEZONE)
    appointment_id = "80000000-0000-4000-8000-000000000001"

    list_blocking_appointments(
        CLINIC_ID,
        [DENTIST_ID],
        start,
        end,
        exclude_appointment_id=appointment_id,
        client=client,  # type: ignore[arg-type]
    )

    assert ("neq", "id", appointment_id) in client.query.filters
