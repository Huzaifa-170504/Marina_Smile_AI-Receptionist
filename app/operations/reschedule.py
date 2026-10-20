from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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
    RescheduleResult,
    RescheduleStatus,
)
from app.operations.appointment_lifecycle import AppointmentStatus, can_transition
from app.operations.availability import generate_candidate_starts, intervals_overlap
from app.operations.booking_models import AppointmentRecord
from app.operations.models import AvailabilitySearchResult, AvailabilityStatus
from app.operations.slot_finder import find_available_slots
from app.repositories.appointments import (
    AppointmentConflictError,
    AppointmentStateError,
    get_appointment_for_contact,
    list_blocking_appointments,
    update_appointment_schedule,
)
from app.repositories.clinics import ClinicNotFoundError, get_active_clinic
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.repositories.schedules import list_active_schedules
from app.repositories.services import list_active_services


logger = logging.getLogger(__name__)


def find_reschedule_slots(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    actor: AppointmentActor,
    dentist_id: str | None = None,
    any_dentist: bool = False,
    limit: int = 5,
    client: Client | None = None,
    calendar_client: CalendarAppointmentChangeClient | None = None,
) -> AvailabilitySearchResult:
    del actor  # Authorization is intentionally an outer-layer concern for non-patients.
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
        normalized_contact_id = str(UUID(str(contact_id)))
        normalized_appointment_id = str(UUID(str(appointment_id)))
        selected_dentist_id = str(UUID(str(dentist_id))) if dentist_id else None
    except ValueError:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason="One or more appointment identifiers are invalid.",
        )

    supabase = client or get_supabase_client()
    appointment = get_appointment_for_contact(
        normalized_clinic_id,
        normalized_contact_id,
        normalized_appointment_id,
        client=supabase,
    )
    if appointment is None:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason="I couldn't find an active appointment matching that request.",
        )
    if (
        appointment.status != AppointmentStatus.CONFIRMED.value
        or not can_transition(appointment.status, AppointmentStatus.CONFIRMED)
    ):
        return AvailabilitySearchResult(
            status=AvailabilityStatus.UNAVAILABLE,
            reason=f"A {appointment.status} appointment cannot be rescheduled.",
        )
    if any_dentist:
        selected_dentist_id = None
    elif selected_dentist_id is None:
        selected_dentist_id = appointment.dentist_id

    return find_available_slots(
        normalized_clinic_id,
        selected_dentist_id,
        appointment.service_id,
        window_start,
        window_end,
        limit,
        exclude_appointment_id=appointment.id,
        exclude_calendar_event_id=appointment.google_calendar_event_id,
        client=supabase,
        calendar_client=calendar_client,
    )


def reschedule_appointment(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    new_start_at: datetime,
    *,
    actor: AppointmentActor,
    new_dentist_id: str | None = None,
    requested_service_id: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
    client: Client | None = None,
    calendar_client: CalendarAppointmentChangeClient | None = None,
) -> RescheduleResult:
    try:
        return _reschedule_appointment(
            clinic_id,
            contact_id,
            appointment_id,
            new_start_at,
            actor=actor,
            new_dentist_id=new_dentist_id,
            requested_service_id=requested_service_id,
            request_id=request_id,
            now=now,
            client=client,
            calendar_client=calendar_client,
        )
    except Exception:
        logger.exception(
            "Unexpected reschedule failure",
            extra={
                "clinic_id": str(clinic_id),
                "contact_id": str(contact_id),
                "appointment_id": str(appointment_id),
                "actor_type": actor.actor_type.value,
                "actor_id": actor.actor_id,
                "operation": "reschedule",
            },
        )
        return RescheduleResult(
            status=RescheduleStatus.ERROR,
            clinic_id=str(clinic_id),
            contact_id=str(contact_id),
            appointment_id=str(appointment_id),
            actor=actor,
            reason="The appointment could not be rescheduled.",
        )


def _reschedule_appointment(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    new_start_at: datetime,
    *,
    actor: AppointmentActor,
    new_dentist_id: str | None,
    requested_service_id: str | None,
    request_id: str | None,
    now: datetime | None,
    client: Client | None,
    calendar_client: CalendarAppointmentChangeClient | None,
) -> RescheduleResult:
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
        normalized_contact_id = str(UUID(str(contact_id)))
        normalized_appointment_id = str(UUID(str(appointment_id)))
        normalized_dentist_id = (
            str(UUID(str(new_dentist_id))) if new_dentist_id else None
        )
        normalized_requested_service_id = (
            str(UUID(str(requested_service_id))) if requested_service_id else None
        )
        normalized_request_id = str(UUID(str(request_id))) if request_id else None
    except ValueError:
        return _minimal_result(
            RescheduleStatus.ERROR,
            str(clinic_id),
            str(contact_id),
            actor,
            appointment_id=str(appointment_id),
            reason="One or more reschedule identifiers are invalid.",
        )
    if not isinstance(new_start_at, datetime) or new_start_at.tzinfo is None:
        return _minimal_result(
            RescheduleStatus.INVALID_TIME,
            normalized_clinic_id,
            normalized_contact_id,
            actor,
            appointment_id=normalized_appointment_id,
            reason="The new appointment time must include a timezone.",
        )

    supabase = client or get_supabase_client()
    appointment = get_appointment_for_contact(
        normalized_clinic_id,
        normalized_contact_id,
        normalized_appointment_id,
        client=supabase,
    )
    if appointment is None:
        return _minimal_result(
            RescheduleStatus.APPOINTMENT_NOT_FOUND,
            normalized_clinic_id,
            normalized_contact_id,
            actor,
            appointment_id=normalized_appointment_id,
            reason="No appointment owned by this contact matched the request.",
        )
    if (
        normalized_requested_service_id is not None
        and normalized_requested_service_id != appointment.service_id
    ):
        return _result(
            RescheduleStatus.CHANGE_SERVICE_UNSUPPORTED,
            appointment,
            actor,
            reason="Rescheduling cannot change the appointment service.",
        )
    if (
        appointment.status != AppointmentStatus.CONFIRMED.value
        or not can_transition(appointment.status, AppointmentStatus.CONFIRMED)
    ):
        return _result(
            RescheduleStatus.NOT_RESCHEDULABLE,
            appointment,
            actor,
            reason=f"A {appointment.status} appointment cannot be rescheduled.",
        )
    if not appointment.google_calendar_event_id:
        return _result(
            RescheduleStatus.CALENDAR_ERROR,
            appointment,
            actor,
            reason="The appointment has no Calendar event to update.",
        )

    try:
        clinic = get_active_clinic(normalized_clinic_id, client=supabase)
    except (ClinicNotFoundError, ValueError):
        return _result(
            RescheduleStatus.ERROR,
            appointment,
            actor,
            reason="The active clinic could not be found.",
        )
    services = list_active_services(normalized_clinic_id, client=supabase)
    service = next((item for item in services if item.id == appointment.service_id), None)
    if service is None or not service.duration_minutes or service.duration_minutes <= 0:
        return _result(
            RescheduleStatus.ERROR,
            appointment,
            actor,
            reason="The appointment service or its duration is unavailable.",
        )
    dentists = list_active_dentists(normalized_clinic_id, client=supabase)
    target_dentist_id = normalized_dentist_id or appointment.dentist_id
    dentist = next((item for item in dentists if item.id == target_dentist_id), None)
    if dentist is None:
        return _result(
            RescheduleStatus.DENTIST_NOT_FOUND,
            appointment,
            actor,
            reason="The requested active dentist was not found.",
        )
    if not filter_dentists_for_service(
        normalized_clinic_id,
        [dentist],
        service.id,
        client=supabase,
    ):
        return _result(
            RescheduleStatus.DENTIST_SERVICE_MISMATCH,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            reason="The requested dentist does not provide this service.",
        )

    try:
        timezone = ZoneInfo(clinic.timezone)
    except Exception:
        return _result(
            RescheduleStatus.ERROR,
            appointment,
            actor,
            reason="The clinic timezone is invalid.",
        )
    local_start = new_start_at.astimezone(timezone)
    new_end_at = local_start + timedelta(minutes=service.duration_minutes)
    current = now or datetime.now(timezone)
    if current.tzinfo is None or local_start <= current.astimezone(timezone):
        return _result(
            RescheduleStatus.INVALID_TIME,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            reason="The new appointment time must be in the future.",
        )

    settings = get_settings()
    schedules = list_active_schedules(
        normalized_clinic_id,
        [dentist.id],
        [local_start.weekday()],
        client=supabase,
    )
    valid_starts = generate_candidate_starts(
        schedules,
        window_start=local_start,
        window_end=new_end_at,
        duration_minutes=service.duration_minutes,
        interval_minutes=settings.availability_slot_interval_minutes,
        timezone=timezone,
    )
    if local_start not in valid_starts:
        return _result(
            RescheduleStatus.OUTSIDE_WORKING_HOURS,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            reason="The selected time is outside the dentist's working hours.",
        )

    database_busy = list_blocking_appointments(
        normalized_clinic_id,
        [dentist.id],
        local_start,
        new_end_at,
        exclude_appointment_id=appointment.id,
        client=supabase,
    )
    if database_busy:
        return _result(
            RescheduleStatus.SLOT_TAKEN,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            reason="The selected interval already has an active appointment.",
        )

    calendar = calendar_client
    if calendar is None:
        try:
            calendar = GoogleCalendarClient.from_default_credentials(
                timeout_seconds=settings.google_calendar_timeout_seconds,
                credentials_file=settings.google_application_credentials,
            )
        except CalendarUnavailableError:
            return _result(
                RescheduleStatus.CALENDAR_ERROR,
                appointment,
                actor,
                reason="Calendar verification is unavailable.",
            )
    try:
        calendar_busy = calendar.list_busy_intervals(
            calendar_id=clinic.google_calendar_id or "",
            clinic_id=normalized_clinic_id,
            dentist_id=dentist.id,
            window_start=local_start,
            window_end=new_end_at,
            exclude_event_id=appointment.google_calendar_event_id,
        )
    except CalendarUnavailableError:
        return _result(
            RescheduleStatus.CALENDAR_ERROR,
            appointment,
            actor,
            reason="Calendar verification is unavailable.",
        )
    if any(
        intervals_overlap(local_start, new_end_at, busy.start_at, busy.end_at)
        for busy in calendar_busy
    ):
        return _result(
            RescheduleStatus.CALENDAR_CONFLICT,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            reason="The selected interval conflicts with the clinic Calendar.",
        )

    try:
        moved = update_appointment_schedule(
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            appointment_id=appointment.id,
            expected_status=appointment.status,
            expected_dentist_id=appointment.dentist_id,
            expected_start_at=appointment.start_at,
            expected_end_at=appointment.end_at,
            new_dentist_id=dentist.id,
            new_start_at=local_start,
            new_end_at=new_end_at,
            client=supabase,
        )
    except AppointmentConflictError:
        return _result(
            RescheduleStatus.SLOT_TAKEN,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            reason="The selected slot was taken by another request.",
        )
    except AppointmentStateError:
        return _result(
            RescheduleStatus.ERROR,
            appointment,
            actor,
            reason="The appointment changed while the request was being processed.",
        )

    try:
        calendar.update_appointment_event(
            calendar_id=clinic.google_calendar_id or "",
            event_id=appointment.google_calendar_event_id,
            clinic_id=normalized_clinic_id,
            clinic_name=clinic.name,
            dentist_id=dentist.id,
            appointment_id=appointment.id,
            contact_id=normalized_contact_id,
            service_id=service.id,
            service_name=service.name,
            start_at=local_start,
            end_at=new_end_at,
            timezone_name=clinic.timezone,
        )
    except Exception:
        logger.exception(
            "Calendar update failed during reschedule",
            extra=_audit_context(
                appointment,
                actor,
                request_id=normalized_request_id,
                new_dentist_id=dentist.id,
                new_start_at=local_start,
                new_end_at=new_end_at,
                new_status=moved.status,
            ),
        )
        try:
            update_appointment_schedule(
                clinic_id=normalized_clinic_id,
                contact_id=normalized_contact_id,
                appointment_id=appointment.id,
                expected_status=moved.status,
                expected_dentist_id=moved.dentist_id,
                expected_start_at=moved.start_at,
                expected_end_at=moved.end_at,
                new_dentist_id=appointment.dentist_id,
                new_start_at=appointment.start_at,
                new_end_at=appointment.end_at,
                client=supabase,
            )
        except Exception:
            logger.exception(
                "Database rollback failed after Calendar reschedule failure",
                extra=_audit_context(
                    appointment,
                    actor,
                    request_id=normalized_request_id,
                    new_dentist_id=dentist.id,
                    new_start_at=local_start,
                    new_end_at=new_end_at,
                    new_status=moved.status,
                ),
            )
            return _result(
                RescheduleStatus.CONSISTENCY_ERROR,
                appointment,
                actor,
                dentist_id=dentist.id,
                dentist_name=dentist.name,
                service_name=service.name,
                new_start_at=local_start,
                new_end_at=new_end_at,
                compensation_succeeded=False,
                reason="Calendar update and database restoration both failed.",
            )
        return _result(
            RescheduleStatus.CALENDAR_ERROR,
            appointment,
            actor,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_name=service.name,
            new_start_at=local_start,
            new_end_at=new_end_at,
            compensation_succeeded=True,
            reason="Calendar update failed and the original appointment was restored.",
        )

    logger.info(
        "Appointment rescheduled",
        extra=_audit_context(
            appointment,
            actor,
            request_id=normalized_request_id,
            new_dentist_id=dentist.id,
            new_start_at=local_start,
            new_end_at=new_end_at,
            new_status=moved.status,
        ),
    )
    return _result(
        RescheduleStatus.RESCHEDULED,
        appointment,
        actor,
        dentist_id=dentist.id,
        dentist_name=dentist.name,
        service_name=service.name,
        new_start_at=local_start,
        new_end_at=new_end_at,
    )


def _audit_context(
    appointment: AppointmentRecord,
    actor: AppointmentActor,
    *,
    request_id: str | None,
    new_dentist_id: str,
    new_start_at: datetime,
    new_end_at: datetime,
    new_status: str,
) -> dict[str, object]:
    return {
        "clinic_id": appointment.clinic_id,
        "contact_id": appointment.contact_id,
        "appointment_id": appointment.id,
        "old_dentist_id": appointment.dentist_id,
        "new_dentist_id": new_dentist_id,
        "service_id": appointment.service_id,
        "old_start_at": appointment.start_at.isoformat(),
        "old_end_at": appointment.end_at.isoformat(),
        "new_start_at": new_start_at.isoformat(),
        "new_end_at": new_end_at.isoformat(),
        "old_status": appointment.status,
        "new_status": new_status,
        "calendar_event_id": appointment.google_calendar_event_id,
        "actor_type": actor.actor_type.value,
        "actor_id": actor.actor_id,
        "actor_reason": actor.reason,
        "request_id": request_id,
        "operation": "reschedule",
    }


def _minimal_result(
    status: RescheduleStatus,
    clinic_id: str,
    contact_id: str,
    actor: AppointmentActor,
    **updates: object,
) -> RescheduleResult:
    return RescheduleResult(
        status=status,
        clinic_id=clinic_id,
        contact_id=contact_id,
        actor=actor,
        **updates,
    )


def _result(
    status: RescheduleStatus,
    appointment: AppointmentRecord,
    actor: AppointmentActor,
    **updates: object,
) -> RescheduleResult:
    values: dict[str, object] = {
        "status": status,
        "clinic_id": appointment.clinic_id,
        "contact_id": appointment.contact_id,
        "actor": actor,
        "appointment_id": appointment.id,
        "dentist_id": appointment.dentist_id,
        "service_id": appointment.service_id,
        "old_start_at": appointment.start_at,
        "old_end_at": appointment.end_at,
        "calendar_event_id": appointment.google_calendar_event_id,
    }
    values.update(updates)
    return RescheduleResult.model_validate(values)
