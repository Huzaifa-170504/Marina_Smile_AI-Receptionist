from typing import Annotated
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.auth import CurrentStaff, require_permission
from app.api.models import DoctorDeactivationRequest, DoctorOnboardingRequest
from app.api.rpc import call_user_rpc
from app.database.supabase import get_supabase_client
from app.integrations.supabase_auth import AuthOperationError
from app.services.staff_onboarding import OnboardingError, StaffOnboardingService


router = APIRouter(prefix="/api/doctors", tags=["dashboard-doctors"])


@router.post("", status_code=201)
async def onboard_doctor(
    request: DoctorOnboardingRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("doctor.manage"))],
) -> dict[str, object]:
    if "staff.manage" not in staff.permissions:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        result = await run_in_threadpool(
            StaffOnboardingService().onboard_doctor,
            staff,
            request,
        )
    except (OnboardingError, AuthOperationError) as exc:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=getattr(exc, "status_code", status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(exc),
        ) from exc
    return result.as_dict()


def _create_approved_deactivation_request(
    staff: CurrentStaff,
    dentist_id: UUID,
    reason: str,
) -> UUID:
    client = get_supabase_client()
    dentist = (
        client.table("dentists")
        .select("id,active")
        .eq("id", str(dentist_id))
        .eq("clinic_id", str(staff.clinic_id))
        .limit(1)
        .execute()
    )
    if not dentist.data:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    request = (
        client.table("doctor_status_change_requests")
        .insert(
            {
                "clinic_id": str(staff.clinic_id),
                "dentist_id": str(dentist_id),
                "requested_by_staff_id": str(staff.id),
                "requested_status": "inactive",
                "reason": reason,
                "status": "pending",
            }
        )
        .execute()
    )
    request_id = UUID(request.data[0]["id"])
    (
        client.table("doctor_status_change_requests")
        .update(
            {
                "status": "approved",
                "reviewed_by_staff_id": str(staff.id),
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", str(request_id))
        .eq("clinic_id", str(staff.clinic_id))
        .eq("status", "pending")
        .execute()
    )
    return request_id


@router.post("/{dentist_id}/deactivate")
async def request_and_apply_deactivation(
    dentist_id: UUID,
    deactivation: DoctorDeactivationRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("doctor.manage"))],
) -> object:
    request_id = await run_in_threadpool(
        _create_approved_deactivation_request,
        staff,
        dentist_id,
        deactivation.reason,
    )
    return await call_user_rpc(
        staff,
        "deactivate_doctor",
        {"p_request_id": str(request_id), "p_owner_staff_id": str(staff.id)},
    )


@router.post("/deactivation-requests/{request_id}/apply")
async def apply_deactivation(
    request_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission("doctor.manage"))],
) -> object:
    return await call_user_rpc(
        staff,
        "deactivate_doctor",
        {"p_request_id": str(request_id), "p_owner_staff_id": str(staff.id)},
    )
