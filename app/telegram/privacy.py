from __future__ import annotations

from typing import Any

from supabase import Client


# The Telegram bot uses the service-role key, so RLS cannot protect it from
# itself. This allowlist is the second boundary: the bot may only touch
# patient-facing operational tables and RPCs, never staff, payroll, audit,
# refund, or internal records.
PATIENT_SAFE_TABLES = frozenset({
    "clinics",
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
})


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
    def storage(self):
        return self._client.storage
