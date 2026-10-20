import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.date_parser import resolve_request_window
from app.operations.intent_parser import parse_operational_request
from app.repositories.clinics import get_active_clinic


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a scheduling request.")
    parser.add_argument("--clinic-id", help="Clinic UUID; defaults to configuration.")
    args = parser.parse_args()

    message = input("Enter patient request: ").strip()
    if not message:
        print("A patient request is required.", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        clinic_id = args.clinic_id or str(settings.default_clinic_id)
        clinic = get_active_clinic(clinic_id, client=get_supabase_client())
        parsed = parse_operational_request(message)
        parsed = resolve_request_window(
            parsed,
            timezone_name=clinic.timezone,
            slot_interval_minutes=settings.availability_slot_interval_minutes,
            earliest_search_days=settings.availability_search_days,
        )
    except Exception as exc:
        print(f"Parsing failed: {exc}", file=sys.stderr)
        return 1

    print(parsed.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
