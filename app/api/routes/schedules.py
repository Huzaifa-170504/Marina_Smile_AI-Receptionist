from datetime import timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.api.auth import CurrentStaff, require_any_permission
from app.api.models import ScheduleOverrideRequest
from app.database.supabase import get_supabase_client


router = APIRouter(prefix="/api/schedule-overrides", tags=["dashboard-schedules"])


def _create_override(staff: CurrentStaff, request: ScheduleOverrideRequest) -> dict[str, object]:
    if "schedule.manage_any" not in staff.permissions:
        if "schedule.manage_own" not in staff.permissions or staff.doctor_id != request.dentist_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if request.end_local <= request.start_local:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="End time must be after start time")

    client = get_supabase_client()
    clinic = client.table("clinics").select("timezone").eq("id", str(staff.clinic_id)).limit(1).execute()
    doctor = (
        client.table("dentists").select("id").eq("clinic_id", str(staff.clinic_id))
        .eq("id", str(request.dentist_id)).eq("active", True).limit(1).execute()
    )
    if not doctor.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active doctor not found")
    try:
        clinic_timezone = ZoneInfo(clinic.data[0]["timezone"])
    except (IndexError, KeyError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Clinic timezone is invalid") from exc

    start_at = request.start_local.replace(tzinfo=clinic_timezone).astimezone(timezone.utc)
    end_at = request.end_local.replace(tzinfo=clinic_timezone).astimezone(timezone.utc)
    result = (
        client.table("schedule_overrides")
        .insert(
            {
                "clinic_id": str(staff.clinic_id),
                "dentist_id": str(request.dentist_id),
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "override_type": request.override_type,
                "reason": request.reason.strip() if request.reason else None,
                "created_by_staff_id": str(staff.id),
            }
        )
        .execute()
    )
    return result.data[0]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_override(
    request: ScheduleOverrideRequest,
    staff: Annotated[
        CurrentStaff,
        Depends(require_any_permission("schedule.manage_any", "schedule.manage_own")),
    ],
) -> dict[str, object]:
    return await run_in_threadpool(_create_override, staff, request)
