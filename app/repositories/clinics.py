from __future__ import annotations

from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import Clinic


class ClinicNotFoundError(LookupError):
    """Raised when an active clinic cannot be found."""


def get_active_clinic(
    clinic_id: str,
    *,
    client: Client | None = None,
) -> Clinic:
    try:
        normalized_id = str(UUID(str(clinic_id)))
    except ValueError as exc:
        raise ValueError("clinic_id must be a valid UUID") from exc

    response = (
        (client or get_supabase_client())
        .table("clinics")
        .select("id,name,timezone,google_calendar_id")
        .eq("id", normalized_id)
        .eq("active", True)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise ClinicNotFoundError("Active clinic not found")
    return Clinic.model_validate(response.data[0])
