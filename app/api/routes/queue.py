from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.auth import CurrentStaff, require_any_permission
from app.api.models import QueueTransitionRequest
from app.api.rpc import call_user_rpc


router = APIRouter(prefix="/api/queue", tags=["dashboard-queue"])


@router.post("/{visit_id}/transition")
async def transition_queue(
    visit_id: UUID,
    transition: QueueTransitionRequest,
    staff: Annotated[
        CurrentStaff,
        Depends(require_any_permission("queue.update_own", "queue.update_all")),
    ],
) -> object:
    return await call_user_rpc(
        staff,
        "transition_queue_state",
        {
            "p_visit_id": str(visit_id),
            "p_expected_status": transition.expected_status,
            "p_target_status": transition.target_status,
            "p_staff_user_id": str(staff.id),
        },
    )
