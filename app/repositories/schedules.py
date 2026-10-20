from __future__ import annotations

from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import DentistSchedule


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
