from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.operations.clinic_hours import (
    BREAK_MESSAGE,
    HOLIDAY_MESSAGE,
    HOURS_MESSAGE,
    WEEKEND_MESSAGE,
    appointment_time_error,
    clinic_date_error,
    slot_within_clinic_hours,
)
from app.operations.models import Clinic


TIMEZONE = ZoneInfo("Asia/Dubai")
CLINIC = Clinic(
    id="10000000-0000-4000-8000-000000000001",
    name="Marina Smile",
    timezone="Asia/Dubai",
)


def _at(hour: int, minute: int, day: int = 17) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TIMEZONE)


def test_weekends_and_holidays_are_closed() -> None:
    saturday = date(2026, 11, 21)
    sunday = date(2026, 11, 22)
    monday = date(2026, 11, 23)

    assert clinic_date_error(CLINIC, saturday, set()) == WEEKEND_MESSAGE
    assert clinic_date_error(CLINIC, sunday, set()) == WEEKEND_MESSAGE
    assert clinic_date_error(CLINIC, monday, {monday}) == HOLIDAY_MESSAGE
    assert clinic_date_error(CLINIC, monday, set()) is None


def test_slots_must_fit_open_hours_and_avoid_break() -> None:
    assert slot_within_clinic_hours(CLINIC, _at(10, 0), _at(10, 30))
    assert not slot_within_clinic_hours(CLINIC, _at(9, 30), _at(10, 0))
    assert not slot_within_clinic_hours(CLINIC, _at(17, 45), _at(18, 15))
    assert slot_within_clinic_hours(CLINIC, _at(17, 30), _at(18, 0))
    assert not slot_within_clinic_hours(CLINIC, _at(12, 45), _at(13, 15))
    assert not slot_within_clinic_hours(CLINIC, _at(13, 0), _at(13, 30))
    assert slot_within_clinic_hours(CLINIC, _at(14, 0), _at(14, 30))


def test_appointment_time_error_returns_specific_messages() -> None:
    assert appointment_time_error(
        CLINIC, _at(9, 0), _at(9, 30), set()
    ) == HOURS_MESSAGE
    assert appointment_time_error(
        CLINIC, _at(12, 45), _at(13, 15), set()
    ) == BREAK_MESSAGE
    assert appointment_time_error(
        CLINIC, _at(10, 0, 21), _at(10, 30, 21), set()
    ) == WEEKEND_MESSAGE
    holiday = date(2026, 11, 23)
    assert appointment_time_error(
        CLINIC, _at(10, 0, 23), _at(10, 30, 23), {holiday}
    ) == HOLIDAY_MESSAGE
    assert appointment_time_error(
        CLINIC, _at(10, 0), _at(10, 30), set()
    ) is None
