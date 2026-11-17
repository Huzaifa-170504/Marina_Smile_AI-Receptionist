from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import Clinic


WEEKEND_MESSAGE = (
    "The clinic is closed on Saturdays and Sundays. "
    "Please select a weekday between Monday and Friday."
)
HOLIDAY_MESSAGE = (
    "The clinic is closed on this public holiday. Please select another available date."
)
HOURS_MESSAGE = "Appointments must be within clinic hours (10:00 AM–6:00 PM)."
BREAK_MESSAGE = (
    "Appointments cannot overlap the clinic break between 1:00 PM and 2:00 PM."
)


def get_clinic_holidays(
    clinic_id: str,
    start_date: date,
    end_date: date,
    *,
    client: Client | None = None,
) -> set[date]:
    supabase = client or get_supabase_client()
    if not hasattr(supabase, "table"):
        # Lightweight test doubles do not implement table queries.
        return set()
    response = (
        supabase.table("clinic_holidays")
        .select("holiday_date")
        .eq("clinic_id", clinic_id)
        .gte("holiday_date", start_date.isoformat())
        .lte("holiday_date", end_date.isoformat())
        .execute()
    )
    return {
        date.fromisoformat(str(row["holiday_date"]))
        for row in (response.data or [])
        if row.get("holiday_date")
    }


def clinic_date_error(
    clinic: Clinic,
    day: date,
    holidays: set[date],
) -> str | None:
    if day.isoweekday() in clinic.closed_weekdays:
        return WEEKEND_MESSAGE
    if day in holidays:
        return HOLIDAY_MESSAGE
    return None


def slot_within_clinic_hours(
    clinic: Clinic,
    start_at: datetime,
    end_at: datetime,
) -> bool:
    timezone = ZoneInfo(clinic.timezone)
    local_start = start_at.astimezone(timezone)
    local_end = end_at.astimezone(timezone)
    if local_start.date() != local_end.date():
        return False
    if local_start.isoweekday() in clinic.closed_weekdays:
        return False
    if local_start.time() < clinic.open_time or local_end.time() > clinic.close_time:
        return False
    if clinic.break_start is not None and clinic.break_end is not None:
        break_start = datetime.combine(
            local_start.date(), clinic.break_start, tzinfo=timezone
        )
        break_end = datetime.combine(
            local_start.date(), clinic.break_end, tzinfo=timezone
        )
        if local_start < break_end and local_end > break_start:
            return False
    return True


def appointment_time_error(
    clinic: Clinic,
    start_at: datetime,
    end_at: datetime,
    holidays: set[date],
) -> str | None:
    timezone = ZoneInfo(clinic.timezone)
    local_start = start_at.astimezone(timezone)
    local_end = end_at.astimezone(timezone)
    day = local_start.date()

    error = clinic_date_error(clinic, day, holidays)
    if error is not None:
        return error

    if local_start.date() != local_end.date():
        return HOURS_MESSAGE
    if local_start.time() < clinic.open_time or local_end.time() > clinic.close_time:
        return HOURS_MESSAGE

    if clinic.break_start is not None and clinic.break_end is not None:
        break_start = datetime.combine(day, clinic.break_start, tzinfo=timezone)
        break_end = datetime.combine(day, clinic.break_end, tzinfo=timezone)
        if local_start < break_end and local_end > break_start:
            return BREAK_MESSAGE
    return None
