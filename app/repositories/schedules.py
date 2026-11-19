from __future__ import annotations

from datetime import datetime
from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import DentistSchedule, ScheduleOverride


def list_active_schedules(
    clinic_id: str,
    dentist_ids: list[str],
    weekdays: list[int],
    *,
    client: Client | None = None,
) -> list[DentistSchedule]:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_dentist_ids = [str(UUID(str(value))) for value in dentist_ids]
    normalized_weekdays = sorted(set(weekdays))
    if not normalized_dentist_ids or not normalized_weekdays:
        return []

    response = (
        (client or get_supabase_client())
        .table("dentist_schedules")
        .select("dentist_id,weekday,start_time,end_time")
        .eq("clinic_id", normalized_clinic_id)
        .eq("active", True)
        .in_("dentist_id", normalized_dentist_ids)
        .in_("weekday", normalized_weekdays)
        .execute()
    )
    return [DentistSchedule.model_validate(row) for row in (response.data or [])]


def list_schedule_overrides(
    clinic_id: str,
    dentist_ids: list[str],
    window_start: datetime,
    window_end: datetime,
    *,
    client: Client | None = None,
) -> list[ScheduleOverride]:
    """Load date-specific availability changes overlapping a requested window."""
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("schedule override window must be timezone-aware")
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_dentist_ids = [str(UUID(str(value))) for value in dentist_ids]
    if not normalized_dentist_ids or window_end <= window_start:
        return []

    response = (
        (client or get_supabase_client())
        .table("schedule_overrides")
        .select("dentist_id,start_at,end_at,override_type")
        .eq("clinic_id", normalized_clinic_id)
        .in_("dentist_id", normalized_dentist_ids)
        .lt("start_at", window_end.isoformat())
        .gt("end_at", window_start.isoformat())
        .execute()
    )
    return [ScheduleOverride.model_validate(row) for row in (response.data or [])]
