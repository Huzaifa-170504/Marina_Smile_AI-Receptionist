from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.google_calendar import (
    CalendarBookingClient,
    CalendarUnavailableError,
    GoogleCalendarClient,
)
from app.operations.availability import generate_candidate_starts, intervals_overlap
from app.operations.booking_models import (
    AppointmentRecord,
    BookingResult,
    BookingStatus,
)
from app.repositories.appointments import (
    AppointmentConflictError,
    AppointmentIdempotencyError,
    cancel_pending_appointment,
    confirm_appointment,
    create_pending_appointment,
    get_appointment_by_booking_request,
    list_blocking_appointments,
)
from app.repositories.clinics import ClinicNotFoundError, get_active_clinic
from app.repositories.contacts import get_contact
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.repositories.schedules import list_active_schedules
from app.repositories.services import list_active_services


logger = logging.getLogger(__name__)


def book_appointment(
    clinic_id: str,
    contact_id: str,
    dentist_id: str,
    service_id: str | None,
    start_at: datetime,
    *,
    booking_request_id: str | None = None,
    now: datetime | None = None,
    client: Client | None = None,
    calendar_client: CalendarBookingClient | None = None,
) -> BookingResult:
    try:
        return _book_appointment(
            clinic_id,
            contact_id,
            dentist_id,
            service_id,
            start_at,
            booking_request_id=booking_request_id,
            now=now,
            client=client,
            calendar_client=calendar_client,
        )
    except Exception:
        logger.exception(
            "Unexpected booking failure",
            extra={
                "clinic_id": str(clinic_id),
                "contact_id": str(contact_id),
                "dentist_id": str(dentist_id),
                "service_id": str(service_id) if service_id else None,
            },
        )
        return BookingResult(
            status=BookingStatus.ERROR,
            clinic_id=str(clinic_id),
            contact_id=str(contact_id),
            dentist_id=str(dentist_id),
            service_id=str(service_id) if service_id else None,
            reason="The booking could not be completed.",
        )


def _book_appointment(
    clinic_id: str,
    contact_id: str,
    dentist_id: str,
    service_id: str | None,
    start_at: datetime,
    *,
    booking_request_id: str | None = None,
    now: datetime | None = None,
    client: Client | None = None,
    calendar_client: CalendarBookingClient | None = None,
) -> BookingResult:
    raw_clinic_id = str(clinic_id)
    if service_id is None:
        return BookingResult(
            status=BookingStatus.NEEDS_SERVICE,
            clinic_id=raw_clinic_id,
            contact_id=str(contact_id),
            dentist_id=str(dentist_id),
            reason="A service is required before booking.",
        )

    try:
        normalized_clinic_id = str(UUID(raw_clinic_id))
        normalized_contact_id = str(UUID(str(contact_id)))
        normalized_dentist_id = str(UUID(str(dentist_id)))
        normalized_service_id = str(UUID(str(service_id)))
        normalized_request_id = (
            str(UUID(str(booking_request_id))) if booking_request_id else None
        )
    except ValueError:
        return BookingResult(
            status=BookingStatus.ERROR,
            clinic_id=raw_clinic_id,
            reason="One or more booking identifiers are invalid.",
        )

    if not isinstance(start_at, datetime) or start_at.tzinfo is None:
        return BookingResult(
            status=BookingStatus.INVALID_TIME,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=normalized_dentist_id,
            service_id=normalized_service_id,
            reason="The appointment time must include a timezone.",
        )

    supabase = client or get_supabase_client()
    try:
        clinic = get_active_clinic(normalized_clinic_id, client=supabase)
    except (ClinicNotFoundError, ValueError):
        return BookingResult(
            status=BookingStatus.ERROR,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=normalized_dentist_id,
            service_id=normalized_service_id,
            reason="The active clinic could not be found.",
        )

    contact = get_contact(
        normalized_clinic_id,
        normalized_contact_id,
        client=supabase,
    )
    if contact is None:
        return BookingResult(
            status=BookingStatus.CONTACT_NOT_FOUND,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=normalized_dentist_id,
            service_id=normalized_service_id,
            reason="The contact was not found for this clinic.",
        )

    dentist = next(
        (
            item
            for item in list_active_dentists(normalized_clinic_id, client=supabase)
            if item.id == normalized_dentist_id
        ),
        None,
    )
    if dentist is None:
        return BookingResult(
            status=BookingStatus.DENTIST_NOT_FOUND,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=normalized_dentist_id,
            service_id=normalized_service_id,
            reason="The active dentist was not found for this clinic.",
        )

    service = next(
        (
            item
            for item in list_active_services(normalized_clinic_id, client=supabase)
            if item.id == normalized_service_id
        ),
        None,
    )
    if service is None:
        return BookingResult(
            status=BookingStatus.SERVICE_NOT_FOUND,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=normalized_service_id,
            reason="The active service was not found for this clinic.",
        )
    if not service.duration_minutes or service.duration_minutes <= 0:
        return BookingResult(
            status=BookingStatus.ERROR,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            reason="The service duration is not configured.",
        )

    eligible = filter_dentists_for_service(
        normalized_clinic_id,
        [dentist],
        service.id,
        client=supabase,
    )
    if not eligible:
        return BookingResult(
            status=BookingStatus.DENTIST_SERVICE_MISMATCH,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            reason="The selected dentist does not provide this service.",
        )

    try:
        timezone = ZoneInfo(clinic.timezone)
    except Exception:
        return BookingResult(
            status=BookingStatus.ERROR,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            reason="The clinic timezone is invalid.",
        )
    local_start = start_at.astimezone(timezone)
    end_at = local_start + timedelta(minutes=service.duration_minutes)

    if normalized_request_id:
        existing = get_appointment_by_booking_request(
            normalized_clinic_id,
            normalized_request_id,
            client=supabase,
        )
        if existing is not None:
            return _result_from_existing(
                existing,
                contact_id=normalized_contact_id,
                dentist_id=dentist.id,
                dentist_name=dentist.name,
                service_id=service.id,
                service_name=service.name,
                start_at=local_start,
            )

    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        return BookingResult(
            status=BookingStatus.INVALID_TIME,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            start_at=local_start,
            end_at=end_at,
            reason="The current time must include a timezone.",
        )
    if local_start <= current.astimezone(timezone):
        return BookingResult(
            status=BookingStatus.INVALID_TIME,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            start_at=local_start,
            end_at=end_at,
            reason="The appointment time must be in the future.",
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
        window_end=end_at,
        duration_minutes=service.duration_minutes,
        interval_minutes=settings.availability_slot_interval_minutes,
        timezone=timezone,
    )
    if local_start not in valid_starts:
        return BookingResult(
            status=BookingStatus.OUTSIDE_WORKING_HOURS,
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            dentist_name=dentist.name,
            service_id=service.id,
            service_name=service.name,
            start_at=local_start,
            end_at=end_at,
            reason="The selected time is outside the dentist's working hours.",
        )

    database_busy = list_blocking_appointments(
        normalized_clinic_id,
        [dentist.id],
        local_start,
        end_at,
        client=supabase,
    )
    if database_busy:
        return _booking_result(
            BookingStatus.SLOT_TAKEN,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "The selected interval already has an active appointment.",
        )

    calendar = calendar_client
    if calendar is None:
        try:
            calendar = GoogleCalendarClient.from_default_credentials(
                timeout_seconds=settings.google_calendar_timeout_seconds,
                credentials_file=settings.google_application_credentials,
            )
        except CalendarUnavailableError:
            return _booking_result(
                BookingStatus.CALENDAR_ERROR,
                normalized_clinic_id,
                normalized_contact_id,
                dentist.id,
                dentist.name,
                service.id,
                service.name,
                local_start,
                end_at,
                "Calendar verification is unavailable.",
            )

    try:
        calendar_busy = calendar.list_busy_intervals(
            calendar_id=clinic.google_calendar_id or "",
            clinic_id=normalized_clinic_id,
            dentist_id=dentist.id,
            window_start=local_start,
            window_end=end_at,
        )
    except CalendarUnavailableError:
        return _booking_result(
            BookingStatus.CALENDAR_ERROR,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "Calendar verification is unavailable.",
        )
    if any(
        intervals_overlap(local_start, end_at, busy.start_at, busy.end_at)
        for busy in calendar_busy
    ):
        return _booking_result(
            BookingStatus.CALENDAR_CONFLICT,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "The selected interval conflicts with the clinic Calendar.",
        )

    try:
        appointment = create_pending_appointment(
            clinic_id=normalized_clinic_id,
            contact_id=normalized_contact_id,
            dentist_id=dentist.id,
            service_id=service.id,
            start_at=local_start,
            end_at=end_at,
            booking_request_id=normalized_request_id,
            client=supabase,
        )
    except AppointmentIdempotencyError:
        existing = (
            get_appointment_by_booking_request(
                normalized_clinic_id,
                normalized_request_id,
                client=supabase,
            )
            if normalized_request_id
            else None
        )
        if existing is not None:
            return _result_from_existing(
                existing,
                contact_id=normalized_contact_id,
                dentist_id=dentist.id,
                dentist_name=dentist.name,
                service_id=service.id,
                service_name=service.name,
                start_at=local_start,
            )
        return _booking_result(
            BookingStatus.ERROR,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "The idempotent booking result could not be loaded.",
        )
    except AppointmentConflictError:
        existing = (
            get_appointment_by_booking_request(
                normalized_clinic_id,
                normalized_request_id,
                client=supabase,
            )
            if normalized_request_id
            else None
        )
        if existing is not None:
            return _result_from_existing(
                existing,
                contact_id=normalized_contact_id,
                dentist_id=dentist.id,
                dentist_name=dentist.name,
                service_id=service.id,
                service_name=service.name,
                start_at=local_start,
            )
        return _booking_result(
            BookingStatus.SLOT_TAKEN,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "The selected slot was reserved by another request.",
        )
    except Exception:
        logger.exception(
            "Pending appointment insert failed",
            extra={
                "clinic_id": normalized_clinic_id,
                "contact_id": normalized_contact_id,
                "dentist_id": dentist.id,
                "service_id": service.id,
                "start_at": local_start.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        return _booking_result(
            BookingStatus.ERROR,
            normalized_clinic_id,
            normalized_contact_id,
            dentist.id,
            dentist.name,
            service.id,
            service.name,
            local_start,
            end_at,
            "The appointment could not be reserved.",
        )

    try:
        event_id = calendar.create_appointment_event(
            calendar_id=clinic.google_calendar_id or "",
            clinic_id=normalized_clinic_id,
            clinic_name=clinic.name,
            dentist_id=dentist.id,
            appointment_id=appointment.id,
            contact_id=normalized_contact_id,
            service_id=service.id,
            service_name=service.name,
            start_at=local_start,
            end_at=end_at,
            timezone_name=clinic.timezone,
        )
    except Exception as exc:
        logger.warning(
            "Calendar event creation failed for appointment %s: %s",
            appointment.id,
            type(exc).__name__,
        )
        try:
            cancel_pending_appointment(
                normalized_clinic_id,
                appointment.id,
                client=supabase,
            )
        except Exception:
            logger.exception(
                "Failed to cancel appointment after Calendar creation failure",
                extra={"appointment_id": appointment.id},
            )
            return _appointment_result(
                BookingStatus.CONSISTENCY_ERROR,
                appointment,
                dentist.name,
                service.name,
                reason="Calendar creation and database compensation both failed.",
            )
        return _appointment_result(
            BookingStatus.CALENDAR_ERROR,
            appointment,
            dentist.name,
            service.name,
            reason="The Calendar event could not be created.",
        )

    try:
        confirmed = confirm_appointment(
            normalized_clinic_id,
            appointment.id,
            event_id,
            client=supabase,
        )
    except Exception:
        logger.exception(
            "Final appointment confirmation failed",
            extra={
                "appointment_id": appointment.id,
                "calendar_event_id": event_id,
            },
        )
        compensation_failed = False
        try:
            calendar.delete_appointment_event(
                calendar_id=clinic.google_calendar_id or "",
                event_id=event_id,
            )
        except Exception:
            compensation_failed = True
            logger.exception(
                "Failed to delete Calendar event after confirmation failure",
                extra={
                    "appointment_id": appointment.id,
                    "calendar_event_id": event_id,
                },
            )
        try:
            cancel_pending_appointment(
                normalized_clinic_id,
                appointment.id,
                client=supabase,
            )
        except Exception:
            compensation_failed = True
            logger.exception(
                "Failed to cancel pending appointment after confirmation failure",
                extra={"appointment_id": appointment.id},
            )
        reason = (
            "Final confirmation failed and compensation requires staff review."
            if compensation_failed
            else "Final confirmation failed and the booking was rolled back."
        )
        return _appointment_result(
            BookingStatus.CONSISTENCY_ERROR,
            appointment,
            dentist.name,
            service.name,
            calendar_event_id=event_id,
            reason=reason,
        )

    logger.info(
        "Appointment confirmed",
        extra={
            "appointment_id": confirmed.id,
            "clinic_id": normalized_clinic_id,
            "contact_id": normalized_contact_id,
            "dentist_id": dentist.id,
            "service_id": service.id,
            "start_at": local_start.isoformat(),
            "end_at": end_at.isoformat(),
            "calendar_event_id": event_id,
            "status": "confirmed",
        },
    )
    return _appointment_result(
        BookingStatus.CONFIRMED,
        confirmed,
        dentist.name,
        service.name,
        calendar_event_id=event_id,
    )


def _result_from_existing(
    appointment: AppointmentRecord,
    *,
    contact_id: str,
    dentist_id: str,
    dentist_name: str,
    service_id: str,
    service_name: str,
    start_at: datetime,
) -> BookingResult:
    if (
        appointment.contact_id != contact_id
        or appointment.dentist_id != dentist_id
        or appointment.service_id != service_id
        or appointment.start_at != start_at
    ):
        return _appointment_result(
            BookingStatus.ERROR,
            appointment,
            dentist_name,
            service_name,
            reason="The booking request ID was already used for different details.",
        )
    if appointment.status == "confirmed":
        status = BookingStatus.CONFIRMED
        reason = None
    elif appointment.status == "pending":
        status = BookingStatus.IN_PROGRESS
        reason = "This booking request is already being processed."
    elif appointment.status == "cancelled":
        status = BookingStatus.CALENDAR_ERROR
        reason = "The earlier booking attempt did not complete."
    else:
        status = BookingStatus.ERROR
        reason = f"The existing appointment is {appointment.status}."
    return _appointment_result(
        status,
        appointment,
        dentist_name,
        service_name,
        calendar_event_id=appointment.google_calendar_event_id,
        reason=reason,
    )


def _booking_result(
    status: BookingStatus,
    clinic_id: str,
    contact_id: str,
    dentist_id: str,
    dentist_name: str,
    service_id: str,
    service_name: str,
    start_at: datetime,
    end_at: datetime,
    reason: str | None,
) -> BookingResult:
    return BookingResult(
        status=status,
        clinic_id=clinic_id,
        contact_id=contact_id,
        dentist_id=dentist_id,
        dentist_name=dentist_name,
        service_id=service_id,
        service_name=service_name,
        start_at=start_at,
        end_at=end_at,
        reason=reason,
    )


def _appointment_result(
    status: BookingStatus,
    appointment: AppointmentRecord,
    dentist_name: str,
    service_name: str,
    *,
    calendar_event_id: str | None = None,
    reason: str | None = None,
) -> BookingResult:
    return BookingResult(
        status=status,
        appointment_id=appointment.id,
        calendar_event_id=calendar_event_id,
        clinic_id=appointment.clinic_id,
        contact_id=appointment.contact_id,
        dentist_id=appointment.dentist_id,
        dentist_name=dentist_name,
        service_id=appointment.service_id,
        service_name=service_name,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        reason=reason,
    )
