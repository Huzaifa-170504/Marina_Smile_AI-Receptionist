import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.auth import CurrentStaff, require_permission
from app.api.models import PaymentApprovalRequest, PaymentRejectionRequest
from app.api.rpc import call_user_rpc
from app.operations.patient_booking import finalize_booking_request


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["dashboard-payments"])


def _booking_request_id(result: object) -> str | None:
    row = result
    if isinstance(row, list):
        row = row[0] if row else None
    if isinstance(row, dict):
        value = row.get("booking_request_id")
        return str(value) if value else None
    return None


@router.post("/{payment_id}/approve")
async def approve_payment(
    payment_id: UUID,
    approval: PaymentApprovalRequest,
    staff: Annotated[CurrentStaff, Depends(require_permission("payment.verify"))],
) -> object:
    payment_result = await call_user_rpc(
        staff,
        "verify_payment",
        {
            "p_payment_id": str(payment_id),
            "p_amount_verified": approval.amount_verified,
            "p_staff_user_id": str(staff.id),
        },
    )
    booking_request_id = _booking_request_id(payment_result)
    finalization: dict = {"status": "not_attempted"}
    if booking_request_id:
        try:
            finalization = await run_in_threadpool(
                finalize_booking_request,
                str(staff.clinic_id),
                booking_request_id,
            )
        except Exception:
            logger.exception(
                "Payment verified but appointment finalization failed",
                extra={"booking_request_id": booking_request_id},
            )
            finalization = {
                "status": "delayed",
                "reason": "Payment verified; appointment confirmation is delayed.",
            }
    return {"payment": payment_result, "finalization": finalization}


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


@router.post("/appointments/{appointment_id}/cash-paid")
async def mark_cash_paid(
    appointment_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission("payment.verify"))],
) -> object:
    return await call_user_rpc(
        staff,
        "mark_cash_payment_paid",
        {"p_appointment_id": str(appointment_id)},
    )
