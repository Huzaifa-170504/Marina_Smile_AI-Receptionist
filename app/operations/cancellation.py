from __future__ import annotations

import logging
from uuid import UUID

from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.google_calendar import (
    CalendarAppointmentChangeClient,
    CalendarUnavailableError,
    GoogleCalendarClient,
)
from app.operations.appointment_change_models import (
    AppointmentActor,
    CancellationResult,
    CancellationStatus,
)
from app.operations.appointment_lifecycle import AppointmentStatus, can_transition
from app.operations.booking_models import AppointmentRecord
from app.repositories.appointments import (
    AppointmentStateError,
    cancel_appointment_record,
    get_appointment_for_contact,
    update_appointment_calendar_event_id,
)
from app.repositories.clinics import ClinicNotFoundError, get_active_clinic
from app.repositories.dentists import list_active_dentists
from app.repositories.services import list_active_services


logger = logging.getLogger(__name__)


def cancel_appointment(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    *,
    actor: AppointmentActor,
    request_id: str | None = None,
    client: Client | None = None,
    calendar_client: CalendarAppointmentChangeClient | None = None,
) -> CancellationResult:
    try:
        return _cancel_appointment(
            clinic_id,
            contact_id,
            appointment_id,
            actor=actor,
            request_id=request_id,
            client=client,
            calendar_client=calendar_client,
        )
    except Exception:
        logger.exception(
            "Unexpected cancellation failure",
            extra={
                "clinic_id": str(clinic_id),
                "contact_id": str(contact_id),
                "appointment_id": str(appointment_id),
                "actor_type": actor.actor_type.value,
                "actor_id": actor.actor_id,
                "operation": "cancel",
            },
        )
        return CancellationResult(
            status=CancellationStatus.ERROR,
            clinic_id=str(clinic_id),
            contact_id=str(contact_id),
            appointment_id=str(appointment_id),
            actor=actor,
            reason="The appointment could not be cancelled.",
        )


def _cancel_appointment(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    *,
    actor: AppointmentActor,
    request_id: str | None,
    client: Client | None,
    calendar_client: CalendarAppointmentChangeClient | None,
) -> CancellationResult:
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
        normalized_contact_id = str(UUID(str(contact_id)))
        normalized_appointment_id = str(UUID(str(appointment_id)))
        normalized_request_id = str(UUID(str(request_id))) if request_id else None
    except ValueError:
        return CancellationResult(
            status=CancellationStatus.ERROR,
            clinic_id=str(clinic_id),
            contact_id=str(contact_id),
            appointment_id=str(appointment_id),
            actor=actor,
            reason="One or more cancellation identifiers are invalid.",
        )

    supabase = client or get_supabase_client()
    appointment = get_appointment_for_contact(
        normalized_clinic_id,
        normalized_contact_id,
        normalized_appointment_id,
        client=supabase,
    )
    if appointment is None:
        return CancellationResult(
            status=CancellationStatus.APPOINTMENT_NOT_FOUND,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            appointment_id=normalized_appointment_id,
            actor=actor,
            reason="No appointment owned by this contact matched the request.",
        )
    if appointment.status == AppointmentStatus.CANCELLED.value:
        return _result(
            CancellationStatus.ALREADY_CANCELLED,
            appointment,
            actor,
            reason="The appointment is already cancelled.",
        )
    if not can_transition(appointment.status, AppointmentStatus.CANCELLED):
        return _result(
            CancellationStatus.NOT_CANCELLABLE,
            appointment,
            actor,
            reason=f"A {appointment.status} appointment cannot be cancelled.",
        )

    try:
        clinic = get_active_clinic(normalized_clinic_id, client=supabase)
    except (ClinicNotFoundError, ValueError):
        return _result(
            CancellationStatus.ERROR,
            appointment,
            actor,
            reason="The active clinic could not be found.",
        )
    dentist = next(
        (
            item
            for item in list_active_dentists(normalized_clinic_id, client=supabase)
            if item.id == appointment.dentist_id
        ),
        None,
    )
    service = next(
        (
            item
            for item in list_active_services(normalized_clinic_id, client=supabase)
            if item.id == appointment.service_id
        ),
        None,
    )
    dentist_name = dentist.name if dentist is not None else "the assigned dentist"
    service_name = service.name if service is not None else "Appointment"

    calendar = calendar_client
    if appointment.google_calendar_event_id and calendar is None:
        settings = get_settings()
        try:
            calendar = GoogleCalendarClient.from_default_credentials(
                timeout_seconds=settings.google_calendar_timeout_seconds,
                credentials_file=settings.google_application_credentials,
            )
        except CalendarUnavailableError:
            return _result(
                CancellationStatus.CALENDAR_ERROR,
                appointment,
                actor,
                dentist_name=dentist_name,
                service_name=service_name,
                reason="Calendar access is unavailable.",
            )

    calendar_deleted = False
    if appointment.google_calendar_event_id:
        assert calendar is not None
        try:
            calendar.delete_appointment_event(
                calendar_id=clinic.google_calendar_id or "",
                event_id=appointment.google_calendar_event_id,
            )
            calendar_deleted = True
        except Exception:
            logger.exception(
                "Calendar deletion failed during cancellation",
                extra=_audit_context(
                    appointment,
                    actor,
                    request_id=normalized_request_id,
                    new_status=appointment.status,
                ),
            )
            return _result(
                CancellationStatus.CALENDAR_ERROR,
                appointment,
                actor,
                dentist_name=dentist_name,
                service_name=service_name,
                reason="The Calendar event could not be cancelled.",
            )

    try:
        cancelled = cancel_appointment_record(
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            appointment_id=appointment.id,
            expected_status=appointment.status,
            client=supabase,
        )
    except AppointmentStateError:
        current = get_appointment_for_contact(
            normalized_clinic_id,
            normalized_contact_id,
            appointment.id,
            client=supabase,
        )
        if current is not None and current.status == AppointmentStatus.CANCELLED.value:
            return _result(
                CancellationStatus.ALREADY_CANCELLED,
                current,
                actor,
                dentist_name=dentist_name,
                service_name=service_name,
                reason="The appointment was already cancelled by another request.",
            )
        return _compensate_database_failure(
            appointment,
            actor,
            clinic_name=clinic.name,
            timezone_name=clinic.timezone,
            calendar_id=clinic.google_calendar_id or "",
            dentist_name=dentist_name,
            service_name=service_name,
            calendar=calendar,
            calendar_deleted=calendar_deleted,
            client=supabase,
            request_id=normalized_request_id,
        )
    except Exception:
        logger.exception(
            "Database cancellation failed",
            extra=_audit_context(
                appointment,
                actor,
                request_id=normalized_request_id,
                new_status=appointment.status,
            ),
        )
        return _compensate_database_failure(
            appointment,
            actor,
            clinic_name=clinic.name,
            timezone_name=clinic.timezone,
            calendar_id=clinic.google_calendar_id or "",
            dentist_name=dentist_name,
            service_name=service_name,
            calendar=calendar,
            calendar_deleted=calendar_deleted,
            client=supabase,
            request_id=normalized_request_id,
        )

    logger.info(
        "Appointment cancelled",
        extra=_audit_context(
            appointment,
            actor,
            request_id=normalized_request_id,
            new_status=cancelled.status,
        ),
    )
    return _result(
        CancellationStatus.CANCELLED,
        cancelled,
        actor,
        dentist_name=dentist_name,
        service_name=service_name,
    )


def _compensate_database_failure(
    appointment: AppointmentRecord,
    actor: AppointmentActor,
    *,
    clinic_name: str,
    timezone_name: str,
    calendar_id: str,
    dentist_name: str,
    service_name: str,
    calendar: CalendarAppointmentChangeClient | None,
    calendar_deleted: bool,
    client: Client,
    request_id: str | None,
) -> CancellationResult:
    if not calendar_deleted or calendar is None:
        return _result(
            CancellationStatus.ERROR,
            appointment,
            actor,
            dentist_name=dentist_name,
            service_name=service_name,
            compensation_succeeded=True,
            reason="The database cancellation failed; the appointment remains active.",
        )
    try:
        restored_event_id = calendar.create_appointment_event(
            calendar_id=calendar_id,
            clinic_id=appointment.clinic_id,
            clinic_name=clinic_name,
            dentist_id=appointment.dentist_id,
            appointment_id=appointment.id,
            contact_id=appointment.contact_id,
            service_id=appointment.service_id,
            service_name=service_name,
            start_at=appointment.start_at,
            end_at=appointment.end_at,
            timezone_name=timezone_name,
        )
        update_appointment_calendar_event_id(
            clinic_id=appointment.clinic_id,
            contact_id=appointment.contact_id,
            appointment_id=appointment.id,
            expected_status=appointment.status,
            calendar_event_id=restored_event_id,
            client=client,
        )
    except Exception:
        logger.exception(
            "Cancellation compensation failed",
            extra=_audit_context(
                appointment,
                actor,
                request_id=request_id,
                new_status=appointment.status,
            ),
        )
        return _result(
            CancellationStatus.CONSISTENCY_ERROR,
            appointment,
            actor,
            dentist_name=dentist_name,
            service_name=service_name,
            compensation_succeeded=False,
            reason="Database cancellation failed and the Calendar event was not restored.",
        )
    return _result(
        CancellationStatus.ERROR,
        appointment,
        actor,
        dentist_name=dentist_name,
        service_name=service_name,
        calendar_event_id=restored_event_id,
        compensation_succeeded=True,
        reason="The database cancellation failed; the Calendar event was restored.",
    )


def _audit_context(
    appointment: AppointmentRecord,
    actor: AppointmentActor,
    *,
    request_id: str | None,
    new_status: str,
) -> dict[str, object]:
    return {
        "clinic_id": appointment.clinic_id,
        "contact_id": appointment.contact_id,
        "appointment_id": appointment.id,
        "old_dentist_id": appointment.dentist_id,
        "new_dentist_id": appointment.dentist_id,
        "service_id": appointment.service_id,
        "old_start_at": appointment.start_at.isoformat(),
        "old_end_at": appointment.end_at.isoformat(),
        "new_start_at": appointment.start_at.isoformat(),
        "new_end_at": appointment.end_at.isoformat(),
        "old_status": appointment.status,
        "new_status": new_status,
        "calendar_event_id": appointment.google_calendar_event_id,
        "actor_type": actor.actor_type.value,
        "actor_id": actor.actor_id,
        "actor_reason": actor.reason,
        "request_id": request_id,
        "operation": "cancel",
    }


def _result(
    status: CancellationStatus,
    appointment: AppointmentRecord,
    actor: AppointmentActor,
    **updates: object,
) -> CancellationResult:
    values: dict[str, object] = {
        "status": status,
        "clinic_id": appointment.clinic_id,
        "contact_id": appointment.contact_id,
        "actor": actor,
        "appointment_id": appointment.id,
        "dentist_id": appointment.dentist_id,
        "service_id": appointment.service_id,
        "start_at": appointment.start_at,
        "end_at": appointment.end_at,
        "calendar_event_id": appointment.google_calendar_event_id,
    }
    values.update(updates)
    return CancellationResult.model_validate(values)
