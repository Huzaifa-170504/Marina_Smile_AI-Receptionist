from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.appointment_change_models import (
    AppointmentActor,
    AppointmentActorType,
)
from app.operations.appointment_change_response import format_cancellation_response
from app.operations.cancellation import cancel_appointment
from app.repositories.appointments import get_appointment_for_contact


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and explicitly confirm one appointment cancellation."
    )
    parser.add_argument("--clinic-id", default=str(get_settings().default_clinic_id))
    parser.add_argument("--contact-id", required=True)
    parser.add_argument("--appointment-id", required=True)
    parser.add_argument("--request-id", default=str(uuid4()))
    parser.add_argument(
        "--actor-type",
        choices=[item.value for item in AppointmentActorType],
        default=AppointmentActorType.PATIENT.value,
    )
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    client = get_supabase_client()
    current = get_appointment_for_contact(
        args.clinic_id,
        args.contact_id,
        args.appointment_id,
        client=client,
    )
    if current is None:
        print("No appointment owned by that contact was found.")
        return 1

    print("Controlled cancellation preview")
    print(f"Appointment: {current.id}")
    print(f"Dentist: {current.dentist_id}")
    print(f"Service: {current.service_id}")
    print(f"Interval: {current.start_at.isoformat()} -> {current.end_at.isoformat()}")
    print(f"Current status: {current.status}")
    print(f"Request ID: {args.request_id}")
    if input("Type y to perform this live cancellation: ").strip().casefold() != "y":
        print("No changes made.")
        return 0

    result = cancel_appointment(
        args.clinic_id,
        args.contact_id,
        args.appointment_id,
        actor=AppointmentActor(
            actor_type=AppointmentActorType(args.actor_type),
            actor_id=args.actor_id,
            reason=args.reason,
        ),
        request_id=args.request_id,
        client=client,
    )
    print(f"Status: {result.status.value}")
    print(format_cancellation_response(result))
    return 0 if result.status.value in {"CANCELLED", "ALREADY_CANCELLED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
