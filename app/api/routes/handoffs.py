from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.api.auth import ROLE_DEFAULTS, CurrentStaff, require_permission
from app.api.models import HandoffStatusRequest
from app.database.supabase import get_supabase_client


router = APIRouter(prefix="/api/handoffs", tags=["dashboard-handoffs"])


def _update_handoff(staff: CurrentStaff, handoff_id: UUID, change: HandoffStatusRequest) -> dict[str, object]:
    client = get_supabase_client()
    if change.assigned_to_staff_id:
        assignee = (
            client.table("staff_users")
            .select("id,role,staff_permission_overrides(permission,allowed)")
            .eq("id", str(change.assigned_to_staff_id))
            .eq("clinic_id", str(staff.clinic_id))
            .eq("active", True)
            .limit(1)
            .execute()
        )
        if not assignee.data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Assigned staff member is invalid or inactive",
            )
        row = assignee.data[0]
        permissions = set(ROLE_DEFAULTS[str(row["role"])])
        for override in row.get("staff_permission_overrides") or []:
            if override["allowed"]:
                permissions.add(override["permission"])
            else:
                permissions.discard(override["permission"])
        if "patient.view_all" not in permissions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Assigned staff member is not authorized to manage handoffs",
            )
    payload: dict[str, object] = {
        "status": change.status,
        "assigned_to_staff_id": str(change.assigned_to_staff_id) if change.assigned_to_staff_id else None,
        "resolved_at": datetime.now(timezone.utc).isoformat() if change.status == "resolved" else None,
    }
    response = (
        client.table("handoffs").update(payload)
        .eq("id", str(handoff_id)).eq("clinic_id", str(staff.clinic_id)).execute()
    )
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Handoff not found")
    return response.data[0]


def _list_assignees(staff: CurrentStaff) -> list[dict[str, str]]:
    response = (
        get_supabase_client().table("staff_users")
        .select("id,full_name,role,staff_permission_overrides(permission,allowed)")
        .eq("clinic_id", str(staff.clinic_id))
        .eq("active", True)
        .order("full_name")
        .execute()
    )
    assignees: list[dict[str, str]] = []
    for row in response.data or []:
        permissions = set(ROLE_DEFAULTS[str(row["role"])])
        for override in row.get("staff_permission_overrides") or []:
            if override["allowed"]:
                permissions.add(override["permission"])
            else:
                permissions.discard(override["permission"])
        if "patient.view_all" in permissions:
            assignees.append({"id": str(row["id"]), "full_name": str(row["full_name"])})
    return assignees


@router.get("/assignees")
async def list_handoff_assignees(
    staff: Annotated[CurrentStaff, Depends(require_permission("patient.view_all"))],
) -> list[dict[str, str]]:
    return await run_in_threadpool(_list_assignees, staff)


@router.patch("/{handoff_id}")
async def update_handoff(
    handoff_id: UUID,
    change: HandoffStatusRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("patient.view_all"))],
) -> dict[str, object]:
    return await run_in_threadpool(_update_handoff, staff, handoff_id, change)
