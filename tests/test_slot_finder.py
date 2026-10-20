from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.integrations.google_calendar import (
    CalendarUnavailableError,
    GoogleCalendarClient,
)
from app.operations import slot_finder
from app.operations.models import (
    AvailabilityStatus,
    BusyInterval,
    Clinic,
    Dentist,
    DentistSchedule,
    Service,
)
from app.repositories.dentists import match_dentists
from app.repositories.services import match_services


TIMEZONE = ZoneInfo("Asia/Dubai")
CLINIC_ID = "10000000-0000-4000-8000-000000000001"
SARA_ID = "20000000-0000-4000-8000-000000000001"
OMAR_ID = "20000000-0000-4000-8000-000000000002"
SERVICE_ID = "30000000-0000-4000-8000-000000000001"
WINDOW_START = datetime(2026, 10, 19, 9, 0, tzinfo=TIMEZONE)
WINDOW_END = datetime(2026, 10, 19, 11, 0, tzinfo=TIMEZONE)


class _Calendar:
    def __init__(self, busy: dict[str, list[BusyInterval]] | None = None) -> None:
        self.busy = busy or {}

    def list_busy_intervals(self, **kwargs: object) -> list[BusyInterval]:
        return list(self.busy.get(str(kwargs["dentist_id"]), []))


class _UnavailableCalendar:
    def list_busy_intervals(self, **_kwargs: object) -> list[BusyInterval]:
        raise CalendarUnavailableError("calendar unavailable")


@pytest.fixture
def configured_finder(monkeypatch: pytest.MonkeyPatch) -> None:
    dentists = [
        Dentist(id=SARA_ID, clinic_id=CLINIC_ID, name="Sara Haddad"),
        Dentist(id=OMAR_ID, clinic_id=CLINIC_ID, name="Omar Ali"),
    ]
    service = Service(
        id=SERVICE_ID,
        clinic_id=CLINIC_ID,
        name="Whitening",
        duration_minutes=45,
    )
    monkeypatch.setattr(
        slot_finder,
        "get_settings",
        lambda: SimpleNamespace(
            generic_availability_minutes=30,
            availability_slot_interval_minutes=15,
            google_calendar_timeout_seconds=10,
            google_application_credentials=None,
            availability_search_days=30,
        ),
    )
    monkeypatch.setattr(
        slot_finder,
        "get_active_clinic",
        lambda _clinic_id, client: Clinic(
            id=CLINIC_ID,
            name="Marina Smile",
            timezone="Asia/Dubai",
            google_calendar_id="calendar@example.com",
        ),
    )
    monkeypatch.setattr(
        slot_finder,
        "list_active_dentists",
        lambda _clinic_id, client: list(dentists),
    )
    monkeypatch.setattr(
        slot_finder,
        "list_active_services",
        lambda _clinic_id, client: [service],
    )
    monkeypatch.setattr(
        slot_finder,
        "filter_dentists_for_service",
        lambda _clinic_id, candidates, _service_id, client: [
            item for item in candidates if item.id == SARA_ID
        ],
    )
    monkeypatch.setattr(
        slot_finder,
        "list_active_schedules",
        lambda _clinic_id, dentist_ids, _weekdays, client: [
            DentistSchedule(
                dentist_id=dentist_id,
                weekday=0,
                start_time=time(9, 0),
                end_time=time(11, 0),
            )
            for dentist_id in dentist_ids
        ],
    )
    monkeypatch.setattr(
        slot_finder,
        "list_blocking_appointments",
        lambda *_args, **_kwargs: [],
    )


def test_name_and_controlled_service_resolution() -> None:
    dentists = [
        Dentist(id=SARA_ID, clinic_id=CLINIC_ID, name="Dr. Sara Haddad"),
        Dentist(id=OMAR_ID, clinic_id=CLINIC_ID, name="Sara Omar"),
    ]
    service = Service(
        id=SERVICE_ID,
        clinic_id=CLINIC_ID,
        name="Scaling and polishing",
    )

    assert match_dentists(dentists, "Sara Haddad") == [dentists[0]]
    assert len(match_dentists(dentists, "Sara")) == 2
    assert match_services([service], "scaling and polishing") == [service]
    assert match_services([service], "clean") == []


def test_service_filters_dentists_and_returns_interval_slots(
    configured_finder: None,
) -> None:
    result = slot_finder.find_available_slots(
        CLINIC_ID,
        None,
        SERVICE_ID,
        WINDOW_START,
        WINDOW_END,
        limit=5,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        calendar_client=_Calendar(),
    )

    assert result.status == AvailabilityStatus.AVAILABLE
    assert {slot.dentist_id for slot in result.slots} == {SARA_ID}
    assert all(slot.duration_minutes == 45 for slot in result.slots)
    assert all(slot.calendar_verified for slot in result.slots)
    assert [slot.start_at.minute for slot in result.slots[:4]] == [0, 15, 30, 45]


def test_calendar_conflict_filters_only_matching_dentist(
    configured_finder: None,
) -> None:
    busy = BusyInterval(
        dentist_id=SARA_ID,
        start_at=datetime(2026, 10, 19, 9, 15, tzinfo=TIMEZONE),
        end_at=datetime(2026, 10, 19, 10, 0, tzinfo=TIMEZONE),
    )
    result = slot_finder.find_available_slots(
        CLINIC_ID,
        SARA_ID,
        None,
        WINDOW_START,
        WINDOW_END,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        calendar_client=_Calendar({SARA_ID: [busy]}),
    )

    assert result.status == AvailabilityStatus.AVAILABLE
    assert all(slot.start_at >= busy.end_at for slot in result.slots)


def test_calendar_failure_returns_unavailable_status(
    configured_finder: None,
) -> None:
    result = slot_finder.find_available_slots(
        CLINIC_ID,
        SARA_ID,
        None,
        WINDOW_START,
        WINDOW_END,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        calendar_client=_UnavailableCalendar(),
    )

    assert result.status == AvailabilityStatus.CALENDAR_UNAVAILABLE
    assert result.slots == []


def test_around_request_preserves_search_window(
    configured_finder: None,
) -> None:
    result = slot_finder.check_availability_request(
        CLINIC_ID,
        "Can Sara see me Monday around 10?",
        now=datetime(2026, 10, 18, 10, 0, tzinfo=TIMEZONE),
        client=SimpleNamespace(),  # type: ignore[arg-type]
        calendar_client=_Calendar(),
    )

    assert result.status == AvailabilityStatus.AVAILABLE
    assert len(result.slots) > 1
    assert result.parsed_request is not None
    assert result.parsed_request.requested_start == datetime(
        2026,
        10,
        19,
        10,
        0,
        tzinfo=TIMEZONE,
    )


def test_concurrent_read_only_checks_may_return_same_slot(
    configured_finder: None,
) -> None:
    def search() -> datetime:
        result = slot_finder.find_available_slots(
            CLINIC_ID,
            SARA_ID,
            None,
            WINDOW_START,
            WINDOW_END,
            limit=1,
            client=SimpleNamespace(),  # type: ignore[arg-type]
            calendar_client=_Calendar(),
        )
        assert result.status == AvailabilityStatus.AVAILABLE
        return result.slots[0].start_at

    with ThreadPoolExecutor(max_workers=2) as executor:
        starts = list(executor.map(lambda _index: search(), range(2)))

    assert starts == [WINDOW_START, WINDOW_START]


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "items": [
                {
                    "status": "confirmed",
                    "start": {"dateTime": "2026-10-19T09:00:00+04:00"},
                    "end": {"dateTime": "2026-10-19T09:30:00+04:00"},
                    "extendedProperties": {
                        "private": {
                            "clinic_id": CLINIC_ID,
                            "dentist_id": SARA_ID,
                            "appointment_id": "appointment-1",
                        }
                    },
                },
                {
                    "status": "confirmed",
                    "start": {"dateTime": "2026-10-19T10:00:00+04:00"},
                    "end": {"dateTime": "2026-10-19T10:30:00+04:00"},
                    "extendedProperties": {
                        "private": {
                            "clinic_id": CLINIC_ID,
                            "dentist_id": OMAR_ID,
                            "appointment_id": "appointment-2",
                        }
                    },
                },
            ]
        }


class _Session:
    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


def test_calendar_client_enforces_private_ownership_metadata() -> None:
    client = GoogleCalendarClient(_Session(), timeout_seconds=10)  # type: ignore[arg-type]

    intervals = client.list_busy_intervals(
        calendar_id="calendar@example.com",
        clinic_id=CLINIC_ID,
        dentist_id=SARA_ID,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert len(intervals) == 1
    assert intervals[0].dentist_id == SARA_ID
