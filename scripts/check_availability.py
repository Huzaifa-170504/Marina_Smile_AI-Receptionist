import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.response_formatter import format_availability_response
from app.operations.slot_finder import check_availability_request
from app.repositories.clinics import get_active_clinic


def main() -> int:
    parser = argparse.ArgumentParser(description="Check operational availability.")
    parser.add_argument("--clinic-id", help="Clinic UUID; defaults to configuration.")
    args = parser.parse_args()

    message = input("Enter patient request: ").strip()
    if not message:
        print("A patient request is required.", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        clinic_id = args.clinic_id or str(settings.default_clinic_id)
        client = get_supabase_client()
        clinic = get_active_clinic(clinic_id, client=client)
        result = check_availability_request(
            clinic_id,
            message,
            client=client,
        )
    except Exception as exc:
        print(f"Availability check failed: {exc}", file=sys.stderr)
        return 1

    print("\nStatus:")
    print(result.status.value)
    print("\nResponse:")
    print(
        format_availability_response(
            result,
            timezone_name=clinic.timezone,
        )
    )
    if result.slots:
        print("\nStructured slots:")
        for slot in result.slots:
            print(slot.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
