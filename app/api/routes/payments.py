from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.auth import CurrentStaff, require_permission
from app.api.models import PaymentApprovalRequest, PaymentRejectionRequest
from app.api.rpc import call_user_rpc


router = APIRouter(prefix="/api/payments", tags=["dashboard-payments"])


@router.post("/{payment_id}/approve")
async def approve_payment(
    payment_id: UUID,
    approval: PaymentApprovalRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("payment.verify"))],
) -> object:
    return await call_user_rpc(
        staff,
        "verify_payment",
        {
            "p_payment_id": str(payment_id),
            "p_amount_verified": approval.amount_verified,
            "p_staff_user_id": str(staff.id),
        },
    )


@router.post("/{payment_id}/reject")
async def reject_payment(
    payment_id: UUID,
    rejection: PaymentRejectionRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("payment.verify"))],
) -> object:
    return await call_user_rpc(
        staff,
        "reject_payment",
        {
            "p_payment_id": str(payment_id),
            "p_reason": rejection.reason,
            "p_staff_user_id": str(staff.id),
        },
    )
