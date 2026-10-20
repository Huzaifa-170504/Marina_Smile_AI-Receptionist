from __future__ import annotations

from datetime import date
from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.appointment_change_models import (
    AppointmentLookupResult,
    AppointmentLookupStatus,
)
from app.repositories.appointments import find_contact_appointments
from app.repositories.clinics import ClinicNotFoundError, get_active_clinic


def find_contact_appointment(
    clinic_id: str,
    contact_id: str,
    *,
    appointment_id: str | None = None,
    dentist_id: str | None = None,
    service_id: str | None = None,
    appointment_date: date | None = None,
    client: Client | None = None,
) -> AppointmentLookupResult:
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
        normalized_contact_id = str(UUID(str(contact_id)))
    except ValueError:
        return AppointmentLookupResult(
            status=AppointmentLookupStatus.APPOINTMENT_NOT_FOUND,
            reason="The clinic or contact identifier is invalid.",
        )

    supabase = client or get_supabase_client()
    try:
        clinic = get_active_clinic(normalized_clinic_id, client=supabase)
        matches = find_contact_appointments(
            normalized_clinic_id,
            normalized_contact_id,
            appointment_id=appointment_id,
            dentist_id=dentist_id,
            service_id=service_id,
            appointment_date=appointment_date,
            timezone_name=clinic.timezone if appointment_date else None,
            client=supabase,
        )
    except (ClinicNotFoundError, ValueError):
        return AppointmentLookupResult(
            status=AppointmentLookupStatus.APPOINTMENT_NOT_FOUND,
            reason="No active appointment matched that request.",
        )
    if not matches:
        return AppointmentLookupResult(
            status=AppointmentLookupStatus.APPOINTMENT_NOT_FOUND,
            reason="No active appointment matched that request.",
        )
    if len(matches) > 1:
        return AppointmentLookupResult(
            status=AppointmentLookupStatus.NEEDS_CLARIFICATION,
            matches=matches,
            reason="More than one active appointment matched that request.",
        )
    return AppointmentLookupResult(
        status=AppointmentLookupStatus.FOUND,
        appointment=matches[0],
        matches=matches,
    )
