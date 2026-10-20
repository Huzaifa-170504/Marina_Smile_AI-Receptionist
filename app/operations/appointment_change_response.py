from __future__ import annotations

from app.operations.appointment_change_models import (
    CancellationResult,
    CancellationStatus,
    RescheduleResult,
    RescheduleStatus,
)


def format_reschedule_response(result: RescheduleResult) -> str:
    if result.status == RescheduleStatus.RESCHEDULED and result.new_start_at:
        service = (result.service_name or "appointment").lower()
        dentist = result.dentist_name or "your dentist"
        when = result.new_start_at.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
        return f"Your {service} appointment with {dentist} has been moved to {when}."
    if result.status == RescheduleStatus.SLOT_TAKEN:
        return "That time is no longer available. Please choose another slot."
    if result.status == RescheduleStatus.APPOINTMENT_NOT_FOUND:
        return "I couldn't find an active appointment matching that request."
    if result.status == RescheduleStatus.NEEDS_CLARIFICATION:
        return result.reason or "What day or time would you prefer?"
    if result.status == RescheduleStatus.NOT_RESCHEDULABLE:
        return "That appointment can no longer be rescheduled."
    if result.status == RescheduleStatus.OUTSIDE_WORKING_HOURS:
        return "That time is outside the dentist's working hours."
    if result.status == RescheduleStatus.CALENDAR_CONFLICT:
        return "That time is not available in the clinic calendar."
    if result.status == RescheduleStatus.CHANGE_SERVICE_UNSUPPORTED:
        return "Changing the treatment requires a separate booking request."
    if result.status in {
        RescheduleStatus.CALENDAR_ERROR,
        RescheduleStatus.CONSISTENCY_ERROR,
    }:
        return "I couldn't safely reschedule that appointment. Clinic staff need to check it."
    return result.reason or "The appointment could not be rescheduled."


def format_cancellation_response(result: CancellationResult) -> str:
    if result.status == CancellationStatus.CANCELLED:
        service = (result.service_name or "appointment").lower()
        dentist = result.dentist_name or "your dentist"
        return f"Your {service} appointment with {dentist} has been cancelled."
    if result.status == CancellationStatus.ALREADY_CANCELLED:
        return "That appointment is already cancelled."
    if result.status == CancellationStatus.APPOINTMENT_NOT_FOUND:
        return "I couldn't find an active appointment matching that request."
    if result.status == CancellationStatus.NEEDS_CLARIFICATION:
        return "I found more than one active appointment. Which one would you like to cancel?"
    if result.status == CancellationStatus.NOT_CANCELLABLE:
        return "That appointment can no longer be cancelled."
    if result.status in {
        CancellationStatus.CALENDAR_ERROR,
        CancellationStatus.CONSISTENCY_ERROR,
    }:
        return "I couldn't safely cancel that appointment. Clinic staff need to check it."
    return result.reason or "The appointment could not be cancelled."
