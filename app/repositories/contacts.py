from __future__ import annotations

from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.booking_models import Contact


def get_contact(
    clinic_id: str,
    contact_id: str,
    *,
    client: Client | None = None,
) -> Contact | None:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_contact_id = str(UUID(str(contact_id)))
    response = (
        (client or get_supabase_client())
        .table("contacts")
        .select("id,clinic_id,name")
        .eq("clinic_id", normalized_clinic_id)
        .eq("id", normalized_contact_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return Contact.model_validate(response.data[0])
