from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError
from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.booking_models import AppointmentRecord
from app.operations.models import BusyInterval


class AppointmentConflictError(RuntimeError):
    """Raised when PostgreSQL rejects an overlapping active appointment."""


class AppointmentIdempotencyError(RuntimeError):
    """Raised when a booking request ID already exists."""


class AppointmentStateError(RuntimeError):
    """Raised when an appointment status transition does not complete."""


def list_blocking_appointments(
    clinic_id: str,
    dentist_ids: list[str],
    window_start: datetime,
    window_end: datetime,
    *,
    exclude_appointment_id: str | None = None,
    client: Client | None = None,
) -> list[BusyInterval]:
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("appointment window must be timezone-aware")
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_dentist_ids = [str(UUID(str(value))) for value in dentist_ids]
    if not normalized_dentist_ids:
        return []

    query = (
        (client or get_supabase_client())
        .table("appointments")
        .select("dentist_id,start_at,end_at")
        .eq("clinic_id", normalized_clinic_id)
        .in_("dentist_id", normalized_dentist_ids)
        .in_("status", ["pending", "confirmed"])
        .lt("start_at", window_end.isoformat())
        .gt("end_at", window_start.isoformat())
    )
    if exclude_appointment_id is not None:
        query = query.neq("id", str(UUID(str(exclude_appointment_id))))
    response = query.execute()
    return [BusyInterval.model_validate(row) for row in (response.data or [])]


def get_appointment_for_contact(
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    *,
    client: Client | None = None,
) -> AppointmentRecord | None:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .select(
            "id,clinic_id,contact_id,dentist_id,service_id,start_at,end_at,status,"
            "google_calendar_event_id,booking_request_id"
        )
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("contact_id", str(UUID(str(contact_id))))
        .eq("id", str(UUID(str(appointment_id))))
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return AppointmentRecord.model_validate(response.data[0])


def get_active_appointments_for_contact(
    clinic_id: str,
    contact_id: str,
    *,
    client: Client | None = None,
) -> list[AppointmentRecord]:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .select(
            "id,clinic_id,contact_id,dentist_id,service_id,start_at,end_at,status,"
            "google_calendar_event_id,booking_request_id"
        )
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("contact_id", str(UUID(str(contact_id))))
        .in_("status", ["pending", "confirmed"])
        .order("start_at")
        .execute()
    )
    return [AppointmentRecord.model_validate(row) for row in (response.data or [])]


def find_contact_appointments(
    clinic_id: str,
    contact_id: str,
    *,
    appointment_id: str | None = None,
    dentist_id: str | None = None,
    service_id: str | None = None,
    appointment_date: date | None = None,
    timezone_name: str | None = None,
    client: Client | None = None,
) -> list[AppointmentRecord]:
    query = (
        (client or get_supabase_client())
        .table("appointments")
        .select(
            "id,clinic_id,contact_id,dentist_id,service_id,start_at,end_at,status,"
            "google_calendar_event_id,booking_request_id"
        )
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("contact_id", str(UUID(str(contact_id))))
        .in_("status", ["pending", "confirmed"])
    )
    if appointment_id is not None:
        query = query.eq("id", str(UUID(str(appointment_id))))
    if dentist_id is not None:
        query = query.eq("dentist_id", str(UUID(str(dentist_id))))
    if service_id is not None:
        query = query.eq("service_id", str(UUID(str(service_id))))
    if appointment_date is not None:
        if not timezone_name:
            raise ValueError("timezone_name is required with appointment_date")
        timezone = ZoneInfo(timezone_name)
        day_start = datetime.combine(appointment_date, time.min, tzinfo=timezone)
        day_end = day_start + timedelta(days=1)
        query = query.gte("start_at", day_start.isoformat()).lt(
            "start_at", day_end.isoformat()
        )
    response = query.order("start_at").execute()
    return [AppointmentRecord.model_validate(row) for row in (response.data or [])]


def get_appointment_by_booking_request(
    clinic_id: str,
    booking_request_id: str,
    *,
    client: Client | None = None,
) -> AppointmentRecord | None:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_request_id = str(UUID(str(booking_request_id)))
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .select(
            "id,clinic_id,contact_id,dentist_id,service_id,start_at,end_at,status,"
            "google_calendar_event_id,booking_request_id"
        )
        .eq("clinic_id", normalized_clinic_id)
        .eq("booking_request_id", normalized_request_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return AppointmentRecord.model_validate(response.data[0])


def create_pending_appointment(
    *,
    clinic_id: str,
    contact_id: str,
    dentist_id: str,
    service_id: str,
    start_at: datetime,
    end_at: datetime,
    booking_request_id: str | None = None,
    client: Client | None = None,
) -> AppointmentRecord:
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("appointment timestamps must be timezone-aware")
    if end_at <= start_at:
        raise ValueError("appointment end must be later than start")

    payload = {
        "clinic_id": str(UUID(str(clinic_id))),
        "contact_id": str(UUID(str(contact_id))),
        "dentist_id": str(UUID(str(dentist_id))),
        "service_id": str(UUID(str(service_id))),
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "status": "pending",
        "booking_request_id": (
            str(UUID(str(booking_request_id))) if booking_request_id else None
        ),
    }
    try:
        response = (
            (client or get_supabase_client())
            .table("appointments")
            .insert(payload)
            .execute()
        )
    except APIError as exc:
        error_text = " ".join(
            value for value in (exc.message, exc.details, exc.hint) if value
        ).casefold()
        if exc.code == "23505" and "booking_request_id" in error_text:
            raise AppointmentIdempotencyError(
                "booking request already exists"
            ) from exc
        if exc.code in {"23P01", "23505"}:
            raise AppointmentConflictError("appointment interval is unavailable") from exc
        raise
    if not response.data:
        raise AppointmentStateError("pending appointment was not returned")
    return AppointmentRecord.model_validate(response.data[0])


def confirm_appointment(
    clinic_id: str,
    appointment_id: str,
    calendar_event_id: str,
    *,
    client: Client | None = None,
) -> AppointmentRecord:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .update(
            {
                "status": "confirmed",
                "google_calendar_event_id": calendar_event_id,
            }
        )
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("id", str(UUID(str(appointment_id))))
        .eq("status", "pending")
        .execute()
    )
    if not response.data:
        raise AppointmentStateError("pending appointment could not be confirmed")
    return AppointmentRecord.model_validate(response.data[0])


def cancel_pending_appointment(
    clinic_id: str,
    appointment_id: str,
    *,
    client: Client | None = None,
) -> AppointmentRecord:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .update({"status": "cancelled"})
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("id", str(UUID(str(appointment_id))))
        .eq("status", "pending")
        .execute()
    )
    if not response.data:
        raise AppointmentStateError("pending appointment could not be cancelled")
    return AppointmentRecord.model_validate(response.data[0])


def update_appointment_schedule(
    *,
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    expected_status: str,
    expected_dentist_id: str,
    expected_start_at: datetime,
    expected_end_at: datetime,
    new_dentist_id: str,
    new_start_at: datetime,
    new_end_at: datetime,
    client: Client | None = None,
) -> AppointmentRecord:
    for value in (expected_start_at, expected_end_at, new_start_at, new_end_at):
        if value.tzinfo is None:
            raise ValueError("appointment timestamps must be timezone-aware")
    if new_end_at <= new_start_at:
        raise ValueError("appointment end must be later than start")

    try:
        response = (
            (client or get_supabase_client())
            .table("appointments")
            .update(
                {
                    "dentist_id": str(UUID(str(new_dentist_id))),
                    "start_at": new_start_at.isoformat(),
                    "end_at": new_end_at.isoformat(),
                }
            )
            .eq("clinic_id", str(UUID(str(clinic_id))))
            .eq("contact_id", str(UUID(str(contact_id))))
            .eq("id", str(UUID(str(appointment_id))))
            .eq("status", expected_status)
            .eq("dentist_id", str(UUID(str(expected_dentist_id))))
            .eq("start_at", expected_start_at.isoformat())
            .eq("end_at", expected_end_at.isoformat())
            .execute()
        )
    except APIError as exc:
        if exc.code == "23P01":
            raise AppointmentConflictError("appointment interval is unavailable") from exc
        raise
    if not response.data:
        raise AppointmentStateError("appointment schedule changed concurrently")
    return AppointmentRecord.model_validate(response.data[0])


def cancel_appointment_record(
    *,
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    expected_status: str,
    client: Client | None = None,
) -> AppointmentRecord:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .update({"status": "cancelled"})
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("contact_id", str(UUID(str(contact_id))))
        .eq("id", str(UUID(str(appointment_id))))
        .eq("status", expected_status)
        .execute()
    )
    if not response.data:
        raise AppointmentStateError("appointment status changed concurrently")
    return AppointmentRecord.model_validate(response.data[0])


def update_appointment_calendar_event_id(
    *,
    clinic_id: str,
    contact_id: str,
    appointment_id: str,
    expected_status: str,
    calendar_event_id: str,
    client: Client | None = None,
) -> AppointmentRecord:
    response = (
        (client or get_supabase_client())
        .table("appointments")
        .update({"google_calendar_event_id": calendar_event_id})
        .eq("clinic_id", str(UUID(str(clinic_id))))
        .eq("contact_id", str(UUID(str(contact_id))))
        .eq("id", str(UUID(str(appointment_id))))
        .eq("status", expected_status)
        .execute()
    )
    if not response.data:
        raise AppointmentStateError("Calendar event ID could not be restored")
    return AppointmentRecord.model_validate(response.data[0])
