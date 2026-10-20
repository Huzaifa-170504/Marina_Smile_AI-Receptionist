from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.google_calendar import (
    CalendarReader,
    CalendarUnavailableError,
    GoogleCalendarClient,
)
from app.operations.availability import (
    generate_candidate_starts,
    remove_conflicting_starts,
)
from app.operations.date_parser import resolve_request_window
from app.operations.intent_parser import IntentParsingError, parse_operational_request
from app.operations.models import (
    AvailabilitySearchResult,
    AvailabilityStatus,
    AvailableSlot,
    OperationalIntent,
)
from app.repositories.appointments import list_blocking_appointments
from app.repositories.clinics import ClinicNotFoundError, get_active_clinic
from app.repositories.dentists import (
    filter_dentists_for_service,
    list_active_dentists,
    match_dentists,
)
from app.repositories.schedules import list_active_schedules
from app.repositories.services import list_active_services, match_services


_EXECUTABLE_INTENTS = {
    OperationalIntent.CHECK_AVAILABILITY,
    OperationalIntent.FIND_SLOTS,
}


def find_available_slots(
    clinic_id: str,
    dentist_id: str | None,
    service_id: str | None,
    window_start: datetime,
    window_end: datetime,
    limit: int = 5,
    *,
    exclude_appointment_id: str | None = None,
    exclude_calendar_event_id: str | None = None,
    client: Client | None = None,
    calendar_client: CalendarReader | None = None,
) -> AvailabilitySearchResult:
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("availability window must be timezone-aware")
    if window_end <= window_start:
        raise ValueError("window_end must be later than window_start")

    normalized_clinic_id = str(UUID(str(clinic_id)))
    supabase = client or get_supabase_client()
    settings = get_settings()
    clinic = get_active_clinic(normalized_clinic_id, client=supabase)
    timezone = ZoneInfo(clinic.timezone)

    dentists = list_active_dentists(normalized_clinic_id, client=supabase)
    if dentist_id is not None:
        normalized_dentist_id = str(UUID(str(dentist_id)))
        dentists = [
            dentist for dentist in dentists if dentist.id == normalized_dentist_id
        ]

    selected_service = None
    duration_minutes = settings.generic_availability_minutes
    if service_id is not None:
        normalized_service_id = str(UUID(str(service_id)))
        services = list_active_services(normalized_clinic_id, client=supabase)
        selected_service = next(
            (service for service in services if service.id == normalized_service_id),
            None,
        )
        if selected_service is None:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.SERVICE_NOT_FOUND,
                reason="The requested service was not found for this clinic.",
            )
        if not selected_service.duration_minutes or selected_service.duration_minutes <= 0:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.NEEDS_CLARIFICATION,
                resolved_service=selected_service.name,
                reason="The service duration needs to be confirmed by clinic staff.",
            )
        duration_minutes = selected_service.duration_minutes
        dentists = filter_dentists_for_service(
            normalized_clinic_id,
            dentists,
            selected_service.id,
            client=supabase,
        )

    resolved_dentist = dentists[0].name if len(dentists) == 1 else None
    resolved_service = selected_service.name if selected_service else None
    if not dentists:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.UNAVAILABLE,
            resolved_service=resolved_service,
            reason="No active eligible dentist was found.",
        )

    local_start = window_start.astimezone(timezone)
    local_end = window_end.astimezone(timezone)
    day_count = (local_end.date() - local_start.date()).days
    weekdays = sorted(
        {
            (local_start.date() + timedelta(days=offset)).weekday()
            for offset in range(day_count + 1)
        }
    )
    dentist_ids = [dentist.id for dentist in dentists]
    schedules = list_active_schedules(
        normalized_clinic_id,
        dentist_ids,
        weekdays,
        client=supabase,
    )
    appointments = list_blocking_appointments(
        normalized_clinic_id,
        dentist_ids,
        window_start,
        window_end,
        exclude_appointment_id=exclude_appointment_id,
        client=supabase,
    )

    reader = calendar_client
    if reader is None:
        try:
            reader = GoogleCalendarClient.from_default_credentials(
                timeout_seconds=settings.google_calendar_timeout_seconds,
                credentials_file=settings.google_application_credentials,
            )
        except CalendarUnavailableError as exc:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.CALENDAR_UNAVAILABLE,
                resolved_dentist=resolved_dentist,
                resolved_service=resolved_service,
                reason=str(exc),
            )

    slots: list[AvailableSlot] = []
    for dentist in dentists:
        dentist_schedules = [
            schedule for schedule in schedules if schedule.dentist_id == dentist.id
        ]
        starts = generate_candidate_starts(
            dentist_schedules,
            window_start=window_start,
            window_end=window_end,
            duration_minutes=duration_minutes,
            interval_minutes=settings.availability_slot_interval_minutes,
            timezone=timezone,
        )
        starts = remove_conflicting_starts(
            starts,
            duration_minutes=duration_minutes,
            busy_intervals=[
                item for item in appointments if item.dentist_id == dentist.id
            ],
        )
        if not starts:
            continue
        try:
            calendar_busy = reader.list_busy_intervals(
                calendar_id=clinic.google_calendar_id or "",
                clinic_id=normalized_clinic_id,
                dentist_id=dentist.id,
                window_start=window_start,
                window_end=window_end,
                exclude_event_id=exclude_calendar_event_id,
            )
        except CalendarUnavailableError as exc:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.CALENDAR_UNAVAILABLE,
                resolved_dentist=resolved_dentist,
                resolved_service=resolved_service,
                reason=str(exc),
            )
        starts = remove_conflicting_starts(
            starts,
            duration_minutes=duration_minutes,
            busy_intervals=calendar_busy,
        )
        for start in starts:
            slots.append(
                AvailableSlot(
                    dentist_id=dentist.id,
                    dentist_name=dentist.name,
                    service_id=selected_service.id if selected_service else None,
                    service_name=resolved_service,
                    start_at=start,
                    end_at=start + timedelta(minutes=duration_minutes),
                    duration_minutes=duration_minutes,
                    calendar_verified=True,
                )
            )

    slots.sort(key=lambda slot: (slot.start_at, slot.dentist_name.casefold()))
    selected_slots = slots[:limit]
    return AvailabilitySearchResult(
        status=(
            AvailabilityStatus.AVAILABLE
            if selected_slots
            else AvailabilityStatus.UNAVAILABLE
        ),
        slots=selected_slots,
        resolved_dentist=resolved_dentist,
        resolved_service=resolved_service,
        reason=None if selected_slots else "No verified slots matched the requested window.",
    )


def check_availability_request(
    clinic_id: str,
    message: str,
    *,
    contact_id: str | None = None,
    now: datetime | None = None,
    limit: int = 5,
    client: Client | None = None,
    calendar_client: CalendarReader | None = None,
) -> AvailabilitySearchResult:
    if contact_id is not None:
        UUID(str(contact_id))
    normalized_clinic_id = str(UUID(str(clinic_id)))
    supabase = client or get_supabase_client()
    try:
        clinic = get_active_clinic(normalized_clinic_id, client=supabase)
    except ClinicNotFoundError as exc:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason=str(exc),
        )

    try:
        parsed = parse_operational_request(message)
    except IntentParsingError:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason="Could you share the preferred day, time, dentist, or service?",
        )

    if parsed.intent not in _EXECUTABLE_INTENTS:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.UNSUPPORTED_INTENT,
            reason=f"{parsed.intent.value} is not supported in this milestone.",
            parsed_request=parsed,
        )

    settings = get_settings()
    parsed = resolve_request_window(
        parsed,
        timezone_name=clinic.timezone,
        now=now,
        slot_interval_minutes=settings.availability_slot_interval_minutes,
        earliest_search_days=settings.availability_search_days,
    )
    if parsed.needs_clarification:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason=parsed.clarification_reason,
            parsed_request=parsed,
        )

    dentists = list_active_dentists(normalized_clinic_id, client=supabase)
    selected_dentist = None
    if parsed.dentist_text:
        dentist_matches = match_dentists(dentists, parsed.dentist_text)
        if not dentist_matches:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.DENTIST_NOT_FOUND,
                reason=f"I couldn't find a dentist matching {parsed.dentist_text}.",
                parsed_request=parsed,
            )
        if len(dentist_matches) > 1:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.NEEDS_CLARIFICATION,
                reason="Which dentist would you like to see?",
                parsed_request=parsed,
            )
        selected_dentist = dentist_matches[0]

    services = list_active_services(normalized_clinic_id, client=supabase)
    selected_service = None
    if parsed.service_text:
        service_matches = match_services(services, parsed.service_text)
        if not service_matches:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.SERVICE_NOT_FOUND,
                reason=f"I couldn't find a service matching {parsed.service_text}.",
                parsed_request=parsed,
            )
        if len(service_matches) > 1:
            return AvailabilitySearchResult(
                status=AvailabilityStatus.NEEDS_CLARIFICATION,
                reason="Which service would you like?",
                parsed_request=parsed,
            )
        selected_service = service_matches[0]

    window_start = parsed.search_window_start
    window_end = parsed.search_window_end
    if window_start is None or window_end is None:
        return AvailabilitySearchResult(
            status=AvailabilityStatus.NEEDS_CLARIFICATION,
            reason="Which day and time would you prefer?",
            parsed_request=parsed,
        )

    is_around_request = bool(
        parsed.requested_time_text
        and "around" in parsed.requested_time_text.casefold()
    )
    if parsed.requested_start is not None and not is_around_request:
        duration = (
            selected_service.duration_minutes
            if selected_service and selected_service.duration_minutes
            else settings.generic_availability_minutes
        )
        window_start = parsed.requested_start
        window_end = parsed.requested_start + timedelta(minutes=duration)

    result = find_available_slots(
        normalized_clinic_id,
        selected_dentist.id if selected_dentist else None,
        selected_service.id if selected_service else None,
        window_start,
        window_end,
        limit,
        client=supabase,
        calendar_client=calendar_client,
    )
    return result.model_copy(
        update={
            "resolved_dentist": (
                selected_dentist.name
                if selected_dentist
                else result.resolved_dentist
            ),
            "resolved_service": (
                selected_service.name if selected_service else None
            ),
            "parsed_request": parsed,
        }
    )
