import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.api.auth import KNOWN_PERMISSIONS, ROLE_DEFAULTS, AuthenticatedStaff, CurrentStaff, require_permission
from app.api.models import (
    PermissionChangeRequest,
    StaffActiveRequest,
    StaffInviteRequest,
    StaffProfileResponse,
    StaffRoleChangeRequest,
)
from app.database.supabase import get_supabase_client
from app.integrations.supabase_auth import AuthOperationError
from app.services.staff_onboarding import OnboardingError, StaffOnboardingService


router = APIRouter(prefix="/api/staff", tags=["dashboard-staff"])
logger = logging.getLogger(__name__)


def _onboarding_error(exc: OnboardingError | AuthOperationError) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", status.HTTP_503_SERVICE_UNAVAILABLE),
        detail=str(exc),
    )


@router.get("/me", response_model=StaffProfileResponse)
def get_me(staff: AuthenticatedStaff) -> StaffProfileResponse:
    return StaffProfileResponse(
        id=staff.id,
        clinic_id=staff.clinic_id,
        auth_user_id=staff.auth_user_id,
        doctor_id=staff.doctor_id,
        full_name=staff.full_name,
        email=staff.email,
        role=staff.role,
        active=staff.active,
        permissions=sorted(staff.permissions),
        overrides=list(staff.overrides),
    )


@router.get("")
async def list_staff(
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> list[dict[str, object]]:
    try:
        return await run_in_threadpool(StaffOnboardingService().list_staff, staff)
    except (OnboardingError, AuthOperationError) as exc:
        raise _onboarding_error(exc) from exc


def _change_permission(
    actor: CurrentStaff,
    target_staff_id: UUID,
    change: PermissionChangeRequest,
) -> None:
    if change.permission not in KNOWN_PERMISSIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown permission")
    client = get_supabase_client()
    target = (
        client.table("staff_users")
        .select("id,clinic_id,role")
        .eq("id", str(target_staff_id))
        .eq("clinic_id", str(actor.clinic_id))
        .limit(1)
        .execute()
    )
    if not target.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    if target_staff_id == actor.id and change.permission == "staff.manage" and change.allowed is False:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Owner cannot remove their own staff management access")

    role = str(target.data[0]["role"])
    role_default = change.permission in ROLE_DEFAULTS[role]
    query = client.table("staff_permission_overrides")
    if change.allowed is None or change.allowed == role_default:
        (
            query.delete()
            .eq("clinic_id", str(actor.clinic_id))
            .eq("staff_user_id", str(target_staff_id))
            .eq("permission", change.permission)
            .execute()
        )
        logger.info(
            "staff_permission_restored_to_role_default",
            extra={
                "clinic_id": str(actor.clinic_id),
                "actor_staff_id": str(actor.id),
                "target_staff_id": str(target_staff_id),
                "permission": change.permission,
            },
        )
        return
    query.upsert(
        {
            "clinic_id": str(actor.clinic_id),
            "staff_user_id": str(target_staff_id),
            "permission": change.permission,
            "allowed": change.allowed,
        },
        on_conflict="staff_user_id,permission",
    ).execute()
    logger.info(
        "staff_permission_override_changed",
        extra={
            "clinic_id": str(actor.clinic_id),
            "actor_staff_id": str(actor.id),
            "target_staff_id": str(target_staff_id),
            "permission": change.permission,
            "allowed": change.allowed,
        },
    )


@router.put("/{target_staff_id}/permissions", status_code=status.HTTP_204_NO_CONTENT)
async def change_permission(
    target_staff_id: UUID,
    change: PermissionChangeRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> None:
    await run_in_threadpool(_change_permission, staff, target_staff_id, change)


@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def invite_staff(
    invitation: StaffInviteRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> dict[str, object]:
    try:
        result = await run_in_threadpool(
            StaffOnboardingService().invite_staff,
            staff,
            invitation,
        )
    except (OnboardingError, AuthOperationError) as exc:
        raise _onboarding_error(exc) from exc
    return result.as_dict()


@router.post("/{target_staff_id}/resend-invitation")
async def resend_invitation(
    target_staff_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> dict[str, str]:
    try:
        return await run_in_threadpool(
            StaffOnboardingService().resend_invitation,
            staff,
            target_staff_id,
        )
    except (OnboardingError, AuthOperationError) as exc:
        raise _onboarding_error(exc) from exc


def _set_active(actor: CurrentStaff, target_staff_id: UUID, active: bool) -> None:
    if target_staff_id == actor.id and not active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="You cannot deactivate your own account")
    client = get_supabase_client()
    target = (
        client.table("staff_users")
        .select("id,doctor_id")
        .eq("clinic_id", str(actor.clinic_id))
        .eq("id", str(target_staff_id))
        .limit(1)
        .execute()
    )
    if not target.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    response = (
        client.table("staff_users")
        .update({"active": active})
        .eq("clinic_id", str(actor.clinic_id))
        .eq("id", str(target_staff_id))
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    doctor_id = target.data[0].get("doctor_id")
    if active and doctor_id:
        doctor = (
            client.table("dentists")
            .select("id,active,deactivation_reason")
            .eq("clinic_id", str(actor.clinic_id))
            .eq("id", str(doctor_id))
            .limit(1)
            .execute()
        )
        if doctor.data and not doctor.data[0]["active"] and doctor.data[0].get("deactivation_reason") == "Inactive at onboarding":
            try:
                doctor_update = (
                    client.table("dentists")
                    .update(
                        {
                            "active": True,
                            "deactivated_at": None,
                            "deactivated_by_staff_id": None,
                            "deactivation_reason": None,
                        }
                    )
                    .eq("clinic_id", str(actor.clinic_id))
                    .eq("id", str(doctor_id))
                    .execute()
                )
                if not doctor_update.data:
                    raise RuntimeError("Doctor activation did not update a record")
            except Exception:
                (
                    client.table("staff_users")
                    .update({"active": False})
                    .eq("clinic_id", str(actor.clinic_id))
                    .eq("id", str(target_staff_id))
                    .execute()
                )
                raise
    logger.info(
        "staff_access_changed",
        extra={
            "clinic_id": str(actor.clinic_id),
            "actor_staff_id": str(actor.id),
            "target_staff_id": str(target_staff_id),
            "active": active,
        },
    )


@router.patch("/{target_staff_id}/active", status_code=status.HTTP_204_NO_CONTENT)
async def set_staff_active(
    target_staff_id: UUID,
    change: StaffActiveRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> None:
    await run_in_threadpool(_set_active, staff, target_staff_id, change.active)


def _change_role(actor: CurrentStaff, target_staff_id: UUID, role: str) -> None:
    if target_staff_id == actor.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="You cannot change your own role")
    client = get_supabase_client()
    target = (
        client.table("staff_users")
        .select("id,role,doctor_id")
        .eq("clinic_id", str(actor.clinic_id))
        .eq("id", str(target_staff_id))
        .limit(1)
        .execute()
    )
    if not target.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    if target.data[0]["role"] == "doctor" or target.data[0].get("doctor_id"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Doctor role changes require the doctor lifecycle workflow",
        )
    client.table("staff_users").update({"role": role}).eq(
        "clinic_id", str(actor.clinic_id)
    ).eq("id", str(target_staff_id)).execute()
    logger.info(
        "staff_role_changed",
        extra={
            "clinic_id": str(actor.clinic_id),
            "actor_staff_id": str(actor.id),
            "target_staff_id": str(target_staff_id),
            "role": role,
        },
    )


@router.patch("/{target_staff_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def change_role(
    target_staff_id: UUID,
    change: StaffRoleChangeRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("staff.manage"))],
) -> None:
    await run_in_threadpool(_change_role, staff, target_staff_id, change.role)
