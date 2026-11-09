from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.google_calendar import GoogleCalendarClient
from app.operations import appointment_notifications
from app.repositories.clinics import get_active_clinic


logger = logging.getLogger(__name__)

ACTIVE_BOOKING_STATUSES = (
    "awaiting_patient_info",
    "awaiting_payment",
    "payment_submitted",
    "payment_rejected",
    "payment_verified",
    "awaiting_slot_selection",
)


class PatientBookingError(RuntimeError):
    """A patient-facing booking error with a safe, user-visible message."""


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def phone_matches(left: str | None, right: str | None) -> bool:
    """Accept local and international forms of the same number.

    Examples that match: +92 311-8917906, 923118917906, 03118917906.
    """
    first = normalize_phone(left or "")
    second = normalize_phone(right or "")
    if not first or not second:
        return False
    if first == second:
        return True
    return len(first) >= 9 and len(second) >= 9 and first[-9:] == second[-9:]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _single_row(data: Any) -> dict | None:
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


def _find_contacts(
    supabase: Client,
    clinic_id: str,
    identifier_type: str,
    identifier: str,
) -> list[dict]:
    rows = (
        supabase.table("contacts")
        .select("id,phone_number,email,telegram_chat_id,name")
        .eq("clinic_id", clinic_id)
        .execute()
        .data
        or []
    )
    matches = []
    for row in rows:
        if identifier_type == "email":
            value = (row.get("email") or "").strip().casefold()
            if value and value == identifier:
                matches.append(row)
        else:
            if phone_matches(row.get("phone_number"), identifier):
                matches.append(row)
    return matches


def _find_contact(
    supabase: Client,
    clinic_id: str,
    identifier_type: str,
    identifier: str,
) -> dict | None:
    matches = _find_contacts(supabase, clinic_id, identifier_type, identifier)
    if len(matches) > 1:
        raise PatientBookingError(
            "More than one clinic contact matches that phone or email. Please contact reception."
        )
    return matches[0] if matches else None


def lookup_identities(
    clinic_id: str,
    identifier_type: str,
    identifier: str,
    *,
    client: Client | None = None,
) -> dict:
    """Find any contact and patients already registered with a phone/email."""
    supabase = client or get_supabase_client()
    contacts = _find_contacts(supabase, clinic_id, identifier_type, identifier)
    if not contacts:
        return {"contacts": [], "patients": []}
    contact_ids = [contact["id"] for contact in contacts]
    patients = (
        supabase.table("patients")
        .select("id,patient_number,full_name,primary_contact_id,active,age_at_registration,gender")
        .eq("clinic_id", clinic_id)
        .in_("primary_contact_id", contact_ids)
        .execute()
        .data
        or []
    )
    return {"contacts": contacts, "patients": patients}


def resolve_or_register_patient(
    clinic_id: str,
    identifier_type: str,
    identifier: str,
    full_name: str,
    *,
    age_at_registration: int | None = None,
    gender: str | None = None,
    telegram_chat_id: int | None = None,
    client: Client | None = None,
) -> dict:
    """Resolve an existing patient or register exactly one patient for the identity.

    The unique contact indexes guarantee that a phone/email/Telegram identity can
    never be attached to a second patient record.
    """
    supabase = client or get_supabase_client()
    name = " ".join(full_name.split())
    if len(name) < 2:
        raise PatientBookingError("Enter your full registered name.")

    contact = _find_contact(supabase, clinic_id, identifier_type, identifier)
    if contact is None:
        whatsapp_id = (
            f"email:{identifier}" if identifier_type == "email" else identifier
        )
        payload = {
            "clinic_id": clinic_id,
            "whatsapp_id": whatsapp_id,
            "phone_number": identifier if identifier_type == "phone" else None,
            "email": identifier if identifier_type == "email" else None,
            "name": name,
            "telegram_chat_id": telegram_chat_id,
        }
        try:
            created = (
                supabase.table("contacts").insert(payload).execute().data or []
            )
        except APIError as exc:
            if exc.code != "23505":
                raise
            contact = _find_contact(supabase, clinic_id, identifier_type, identifier)
            if contact is None:
                raise PatientBookingError(
                    "That phone or email is already registered. Please contact reception."
                ) from exc
            created = [contact]
        contact = created[0]

    if (
        telegram_chat_id is not None
        and contact.get("telegram_chat_id") is not None
        and contact.get("telegram_chat_id") != telegram_chat_id
    ):
        raise PatientBookingError(
            "This phone or email is linked to another Telegram account. Please contact reception."
        )

    if telegram_chat_id is not None and contact.get("telegram_chat_id") != telegram_chat_id:
        try:
            supabase.table("contacts").update(
                {"telegram_chat_id": telegram_chat_id}
            ).eq("clinic_id", clinic_id).eq("id", contact["id"]).execute()
        except APIError as exc:
            if exc.code != "23505":
                raise
            raise PatientBookingError(
                "This Telegram account is already linked to another patient. Please contact reception."
            ) from exc

    patients = (
        supabase.table("patients")
        .select("id,patient_number,full_name,primary_contact_id,active,age_at_registration,gender")
        .eq("clinic_id", clinic_id)
        .eq("primary_contact_id", contact["id"])
        .execute()
        .data
        or []
    )
    for patient in patients:
        if normalize_name(patient["full_name"]) == normalize_name(name):
            updates = {}
            if age_at_registration is not None and patient.get("age_at_registration") is None:
                updates["age_at_registration"] = age_at_registration
            if gender is not None and not patient.get("gender"):
                updates["gender"] = gender
            if updates:
                supabase.table("patients").update(updates).eq(
                    "clinic_id", clinic_id
                ).eq("id", patient["id"]).execute()
                patient.update(updates)
            return {
                "clinic_id": clinic_id,
                "contact_id": contact["id"],
                "patient_id": patient["id"],
                "patient_number": patient.get("patient_number"),
                "name": patient["full_name"],
                "age_at_registration": patient.get("age_at_registration"),
                "gender": patient.get("gender"),
                "telegram_chat_id": contact.get("telegram_chat_id") or telegram_chat_id,
            }

    if patients:
        raise PatientBookingError(
            f"That phone or email is already registered to {patients[0]['full_name']}. "
            "A second patient cannot be registered under the same phone or email."
        )

    created_patients = (
        supabase.table("patients")
        .insert(
            {
                "clinic_id": clinic_id,
                "primary_contact_id": contact["id"],
                "full_name": name,
                "gender": gender or "other",
                "age_at_registration": age_at_registration,
                "active": True,
            }
        )
        .execute()
        .data
        or []
    )
    if not created_patients:
        raise PatientBookingError("Your patient record could not be created.")
    patient = created_patients[0]
    return {
        "clinic_id": clinic_id,
        "contact_id": contact["id"],
        "patient_id": patient["id"],
        "patient_number": patient.get("patient_number"),
        "name": patient["full_name"],
        "age_at_registration": patient.get("age_at_registration"),
        "gender": patient.get("gender"),
        "telegram_chat_id": contact.get("telegram_chat_id") or telegram_chat_id,
    }


def active_booking_for_patient(
    clinic_id: str,
    patient_id: str,
    *,
    client: Client | None = None,
) -> dict | None:
    """Return the patient's next unserved booking, if one exists."""
    supabase = client or get_supabase_client()
    now = _utc_now().isoformat()

    requests = (
        supabase.table("booking_requests")
        .select("id,status,preferred_window_start,total_amount,currency,payment_mode,payment_status")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .in_("status", list(ACTIVE_BOOKING_STATUSES))
        .execute()
        .data
        or []
    )
    if requests:
        return {"kind": "request", "record": requests[0]}

    appointments = (
        supabase.table("appointments")
        .select(
            "id,appointment_reference,status,start_at,end_at,total_amount,currency,payment_mode,payment_status"
        )
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .in_("status", ["pending", "confirmed"])
        .gte("end_at", now)
        .order("start_at")
        .limit(1)
        .execute()
        .data
        or []
    )
    if appointments:
        return {"kind": "appointment", "record": appointments[0]}
    return None


def describe_active_booking(booking: dict) -> str:
    record = booking["record"]
    when = record.get("preferred_window_start") or record.get("start_at")
    when_text = when[:16].replace("T", " ") if when else "a scheduled time"
    return (
        f"You already have an unserved booking ({when_text}).\n"
        "You cannot edit the selected services in an existing booking.\n"
        "Open My Appointments to retry confirmation or cancel the existing booking, then book again."
    )


def create_booking_request(
    *,
    clinic_id: str,
    patient: dict,
    services: list[dict],
    dentist_id: str,
    start_at: datetime,
    end_at: datetime,
    payment_mode: str,
    client: Client | None = None,
) -> dict:
    if not services:
        raise PatientBookingError("Select at least one service.")
    if payment_mode not in {"cash", "online"}:
        raise PatientBookingError("Choose cash in hand or online payment.")

    supabase = client or get_supabase_client()
    currency = services[0].get("currency") or "AED"
    total_amount = sum(float(service.get("price_amount") or 0) for service in services)
    status = "awaiting_payment" if payment_mode == "online" else "awaiting_slot_selection"
    request_id = str(uuid4())

    payload = {
        "id": request_id,
        "clinic_id": clinic_id,
        "patient_id": patient["patient_id"],
        "contact_id": patient["contact_id"],
        "service_id": services[0]["service_id"],
        "preferred_dentist_id": dentist_id,
        "preferred_date": start_at.date().isoformat(),
        "preferred_window_start": start_at.isoformat(),
        "preferred_window_end": end_at.isoformat(),
        "status": status,
        "payment_mode": payment_mode,
        "payment_status": "unpaid",
        "total_amount": total_amount,
        "currency": currency,
        "idempotency_key": str(uuid4()),
    }
    created = supabase.table("booking_requests").insert(payload).execute().data or []
    if not created:
        raise PatientBookingError("The booking request could not be saved.")

    lines = [
        {
            "clinic_id": clinic_id,
            "booking_request_id": request_id,
            "service_id": service["service_id"],
            "service_name": service["name"],
            "price_amount": service.get("price_amount") or 0,
            "duration_minutes": service["duration_minutes"],
            "currency": service.get("currency") or currency,
            "dentist_id": service.get("dentist_id") or dentist_id,
            "start_at": service.get("start_at"),
            "end_at": service.get("end_at"),
        }
        for service in services
    ]
    supabase.table("booking_request_services").insert(lines).execute()
    return created[0]


def _booking_lines(
    supabase: Client, clinic_id: str, request: dict
) -> list[dict]:
    lines = (
        supabase.table("booking_request_services")
        .select(
            "service_id,service_name,price_amount,duration_minutes,currency,"
            "dentist_id,start_at,end_at"
        )
        .eq("clinic_id", clinic_id)
        .eq("booking_request_id", request["id"])
        .execute()
        .data
        or []
    )
    if lines:
        return lines
    service = (
        supabase.table("services")
        .select("id,name,price_from,duration_minutes,currency")
        .eq("clinic_id", clinic_id)
        .eq("id", request["service_id"])
        .limit(1)
        .execute()
        .data
        or []
    )
    if not service:
        return []
    row = service[0]
    return [
        {
            "service_id": row["id"],
            "service_name": row["name"],
            "price_amount": row.get("price_from") or 0,
            "duration_minutes": row["duration_minutes"],
            "currency": row.get("currency") or "AED",
            "dentist_id": request.get("preferred_dentist_id"),
            "start_at": request.get("preferred_window_start"),
            "end_at": request.get("preferred_window_end"),
        }
    ]


def _apply_service_schedule(
    supabase: Client,
    clinic_id: str,
    appointment_id: str,
    services: list[dict],
) -> None:
    """Copy per-service doctor/time from the request onto the appointment."""
    for line in services:
        if not line.get("start_at") or not line.get("end_at"):
            continue
        supabase.table("appointment_services").update(
            {
                "dentist_id": line.get("dentist_id"),
                "start_at": line["start_at"],
                "end_at": line["end_at"],
            }
        ).eq("clinic_id", clinic_id).eq("appointment_id", appointment_id).eq(
            "service_id", line["service_id"]
        ).execute()


def finalize_booking_request(
    clinic_id: str,
    booking_request_id: str,
    *,
    client: Client | None = None,
    calendar_client: Any | None = None,
) -> dict:
    """Reserve and confirm an approved or cash booking, creating its reminder."""
    supabase = client or get_supabase_client()
    request_response = (
        supabase.table("booking_requests")
        .select(
            "id,clinic_id,patient_id,contact_id,service_id,preferred_dentist_id,"
            "preferred_window_start,preferred_window_end,status,payment_mode,"
            "payment_status,total_amount,currency"
        )
        .eq("clinic_id", clinic_id)
        .eq("id", booking_request_id)
        .limit(1)
        .execute()
    )
    request = _single_row(request_response.data)
    if request is None:
        return {"status": "not_found", "reason": "Booking request not found."}

    existing = (
        supabase.table("appointments")
        .select(
            "id,appointment_reference,status,start_at,end_at,payment_mode,payment_status,total_amount,currency"
        )
        .eq("clinic_id", clinic_id)
        .eq("booking_request_id", booking_request_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        appointment = existing[0]
        if appointment["status"] == "confirmed":
            services = _booking_lines(supabase, clinic_id, request)
            _apply_service_schedule(supabase, clinic_id, appointment["id"], services)
            return {
                "status": "confirmed",
                "appointment": appointment,
                "request": request,
                "services": services,
            }
        return {
            "status": "in_progress",
            "appointment": appointment,
            "request": request,
            "reason": "The appointment reservation is already being processed.",
        }

    if request["status"] not in {"payment_verified", "awaiting_slot_selection"}:
        return {
            "status": "not_ready",
            "request": request,
            "reason": "This booking is not ready to be confirmed yet.",
        }
    if not request.get("preferred_dentist_id") or not request.get("preferred_window_start") or not request.get(
        "preferred_window_end"
    ):
        return {
            "status": "missing_slot",
            "request": request,
            "reason": "The selected time is missing. Please contact reception.",
        }

    services = _booking_lines(supabase, clinic_id, request)
    payment_id = None
    if request.get("payment_mode") != "cash":
        payments = (
            supabase.table("payments")
            .select("id")
            .eq("clinic_id", clinic_id)
            .eq("booking_request_id", booking_request_id)
            .eq("patient_id", request["patient_id"])
            .eq("status", "verified")
            .order("verified_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not payments:
            return {
                "status": "awaiting_payment",
                "request": request,
                "reason": "The payment is not verified yet.",
            }
        payment_id = payments[0]["id"]

    dentist_rows = (
        supabase.table("dentists")
        .select("id,name")
        .eq("clinic_id", clinic_id)
        .eq("id", request["preferred_dentist_id"])
        .limit(1)
        .execute()
        .data
        or []
    )
    if not dentist_rows:
        return {
            "status": "missing_dentist",
            "request": request,
            "reason": "The selected doctor could not be loaded.",
        }
    dentist_name = dentist_rows[0]["name"]
    service_names = ", ".join(line["service_name"] for line in services) or "Appointment"

    reserved = (
        supabase.rpc(
            "reserve_appointment",
            {
                "p_clinic_id": clinic_id,
                "p_booking_request_id": request["id"],
                "p_payment_id": payment_id,
                "p_dentist_id": request["preferred_dentist_id"],
                "p_start_at": request["preferred_window_start"],
                "p_end_at": request["preferred_window_end"],
                "p_idempotency_key": request["id"],
            },
        )
        .execute()
        .data
    )
    appointment = _single_row(reserved)
    if appointment is None:
        return {
            "status": "error",
            "request": request,
            "reason": "The appointment could not be reserved.",
        }

    clinic = get_active_clinic(clinic_id, client=supabase)
    if appointment.get("status") != "confirmed":
        calendar = calendar_client
        if calendar is None:
            settings = get_settings()
            calendar = GoogleCalendarClient.from_default_credentials(
                timeout_seconds=settings.google_calendar_timeout_seconds,
                credentials_file=settings.google_application_credentials,
            )
        try:
            event_id = calendar.create_appointment_event(
                calendar_id=clinic.google_calendar_id or "",
                clinic_id=clinic_id,
                clinic_name=clinic.name,
                dentist_id=request["preferred_dentist_id"],
                appointment_id=appointment["id"],
                contact_id=request["contact_id"],
                service_id=request["service_id"],
                service_name=service_names,
                start_at=_parse_timestamp(request["preferred_window_start"]),
                end_at=_parse_timestamp(request["preferred_window_end"]),
                timezone_name=clinic.timezone,
            )
        except Exception:
            logger.exception(
                "Calendar event creation failed during booking finalization",
                extra={"booking_request_id": booking_request_id},
            )
            return {
                "status": "calendar_error",
                "request": request,
                "appointment": appointment,
                "reason": "The appointment was reserved, but Calendar confirmation is delayed.",
            }

        confirmed = (
            supabase.rpc(
                "confirm_appointment_reservation",
                {
                    "p_clinic_id": clinic_id,
                    "p_appointment_id": appointment["id"],
                    "p_google_calendar_event_id": event_id,
                },
            )
            .execute()
            .data
        )
        appointment = _single_row(confirmed) or appointment

    _apply_service_schedule(supabase, clinic_id, appointment["id"], services)

    if request.get("payment_mode") == "online":
        supabase.table("booking_requests").update({"payment_status": "paid"}).eq(
            "clinic_id", clinic_id
        ).eq("id", booking_request_id).execute()
        supabase.table("appointments").update({"payment_status": "paid"}).eq(
            "clinic_id", clinic_id
        ).eq("id", appointment["id"]).execute()
        request["payment_status"] = "paid"
        appointment["payment_status"] = "paid"

    template = {
        "appointment_reference": appointment.get("appointment_reference"),
        "start_at": request.get("preferred_window_start"),
        "end_at": request.get("preferred_window_end"),
        "clinic_name": clinic.name,
        "timezone": clinic.timezone,
        "dentist_name": dentist_name,
        "services": ", ".join(line["service_name"] for line in services),
        "payment_mode": request.get("payment_mode"),
        "payment_status": request.get("payment_status"),
    }
    appointment_notifications.record_immediate_notification(
        supabase,
        clinic_id=clinic_id,
        contact_id=request.get("contact_id"),
        booking_request_id=request["id"],
        appointment_id=appointment["id"],
        notification_type="booking_confirmation",
        template_data=template,
        event_key=f"booking_confirmation:{appointment['id']}",
    )
    return {
        "status": "confirmed",
        "appointment": appointment,
        "request": request,
        "services": services,
        "dentist_name": dentist_name,
    }
