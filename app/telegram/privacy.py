from __future__ import annotations

from typing import Any

from supabase import Client


# The Telegram bot uses the service-role key, so RLS cannot protect it from
# itself. This allowlist is the second boundary: the bot may only touch
# patient-facing operational tables and RPCs, never staff, payroll, audit,
# refund, or internal records.
PATIENT_SAFE_TABLES = frozenset({
    "clinics",
    "clinic_holidays",
    "services",
    "dentists",
    "dentist_services",
    "dentist_schedules",
    "contacts",
    "patients",
    "booking_requests",
    "booking_request_services",
    "appointments",
    "appointment_services",
    "appointment_visits",
    "payments",
    "outbound_notifications",
})

PATIENT_SAFE_RPCS = frozenset({
    "transition_booking_request",
    "anonymize_cancelled_booking",
    "reserve_appointment",
    "confirm_appointment_reservation",
})

# The receipt bucket is the only Storage surface the patient flow needs.
PATIENT_SAFE_BUCKETS = frozenset({"payment-proofs"})


class PatientSafeSupabase:
    """Restrict a service-role Supabase client to patient-safe capabilities."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def table(self, name: str):
        if name not in PATIENT_SAFE_TABLES:
            raise PermissionError(
                f"Telegram bot access to table '{name}' is blocked by the privacy allowlist."
            )
        return self._client.table(name)

    def rpc(self, function_name: str, params: dict[str, Any] | None = None):
        if function_name not in PATIENT_SAFE_RPCS:
            raise PermissionError(
                f"Telegram bot access to RPC '{function_name}' is blocked by the privacy allowlist."
            )
        return self._client.rpc(function_name, params or {})

    @property
    def storage(self) -> "_PatientSafeStorage":
        return _PatientSafeStorage(self._client.storage)


class _PatientSafeStorage:
    """Restrict Storage access to the patient-facing buckets in the allowlist."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def from_(self, bucket: str):
        if bucket not in PATIENT_SAFE_BUCKETS:
            raise PermissionError(
                f"Telegram bot access to storage bucket '{bucket}' is blocked by the privacy allowlist."
            )
        return self._storage.from_(bucket)
