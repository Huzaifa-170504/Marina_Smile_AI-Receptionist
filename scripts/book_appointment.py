from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.booking import book_appointment
from app.operations.booking_response import format_booking_response
from app.repositories.clinics import get_active_clinic
from app.repositories.contacts import get_contact
from app.repositories.dentists import list_active_dentists
from app.repositories.services import list_active_services


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one controlled test booking.")
    parser.add_argument("--clinic-id", help="Clinic UUID; defaults to configuration.")
    parser.add_argument("--contact-id")
    parser.add_argument("--dentist-id")
    parser.add_argument("--service-id")
    parser.add_argument("--start-at", help="Timezone-aware ISO 8601 date/time.")
    parser.add_argument("--booking-request-id", help="Optional idempotency UUID.")
    args = parser.parse_args()

    contact_id = args.contact_id or input("Contact ID: ").strip()
    dentist_id = args.dentist_id or input("Dentist ID: ").strip()
    service_id = args.service_id or input("Service ID: ").strip()
    start_text = args.start_at or input(
        "Date/time (ISO 8601 with timezone): "
    ).strip()
    if not all((contact_id, dentist_id, service_id, start_text)):
        print("Contact, dentist, service, and date/time are required.", file=sys.stderr)
        return 1

    try:
        start_at = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        if start_at.tzinfo is None:
            raise ValueError("timezone offset is missing")
        settings = get_settings()
        clinic_id = args.clinic_id or str(settings.default_clinic_id)
        client = get_supabase_client()
        clinic = get_active_clinic(clinic_id, client=client)
        contact = get_contact(clinic_id, contact_id, client=client)
        dentist = next(
            (
                item
                for item in list_active_dentists(clinic_id, client=client)
                if item.id == dentist_id
            ),
            None,
        )
        service = next(
            (
                item
                for item in list_active_services(clinic_id, client=client)
                if item.id == service_id
            ),
            None,
        )
        if contact is None or dentist is None or service is None:
            raise ValueError("Contact, dentist, or service was not found for the clinic")
        if not service.duration_minutes or service.duration_minutes <= 0:
            raise ValueError("Service duration is not configured")
        timezone = ZoneInfo(clinic.timezone)
        local_start = start_at.astimezone(timezone)
        end_at = local_start + timedelta(minutes=service.duration_minutes)
        booking_request_id = args.booking_request_id or str(uuid4())
    except Exception as exc:
        print(f"Booking preparation failed: {exc}", file=sys.stderr)
        return 1

    print("\nBooking preview:")
    print(f"Clinic: {clinic.name} ({clinic.id})")
    print(f"Contact: {contact.name or contact.id} ({contact.id})")
    print(f"Dentist: {dentist.name} ({dentist.id})")
    print(f"Service: {service.name} ({service.id})")
    print(f"Start: {local_start.isoformat()}")
    print(f"End: {end_at.isoformat()}")
    print(f"Calendar ID: {clinic.google_calendar_id or 'NOT CONFIGURED'}")
    print(f"Booking request ID: {booking_request_id}")

    if input("\nProceed with test booking? [y/N] ").strip().casefold() != "y":
        print("Booking cancelled; no appointment or Calendar event was created.")
        return 0

    result = book_appointment(
        clinic.id,
        contact.id,
        dentist.id,
        service.id,
        local_start,
        booking_request_id=booking_request_id,
        client=client,
    )
    print(f"\nStatus: {result.status.value}")
    print(format_booking_response(result, timezone_name=clinic.timezone))
    print(result.model_dump_json(indent=2))
    return 0 if result.status.value == "CONFIRMED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
