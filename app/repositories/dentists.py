from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import Dentist


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\bdr\.?\s*", "", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def list_active_dentists(
    clinic_id: str,
    *,
    client: Client | None = None,
) -> list[Dentist]:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    response = (
        (client or get_supabase_client())
        .table("dentists")
        .select("id,clinic_id,name")
        .eq("clinic_id", normalized_clinic_id)
        .eq("active", True)
        .execute()
    )
    return [Dentist.model_validate(row) for row in (response.data or [])]


def match_dentists(dentists: list[Dentist], name_text: str) -> list[Dentist]:
    requested = _normalize_name(name_text)
    if not requested:
        return []

    exact = [dentist for dentist in dentists if _normalize_name(dentist.name) == requested]
    if exact:
        return exact

    matches = []
    for dentist in dentists:
        name_parts = _normalize_name(dentist.name).split()
        if requested in name_parts:
            matches.append(dentist)
    return matches


def filter_dentists_for_service(
    clinic_id: str,
    dentists: list[Dentist],
    service_id: str,
    *,
    client: Client | None = None,
) -> list[Dentist]:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_service_id = str(UUID(str(service_id)))
    tenant_dentists = [
        dentist for dentist in dentists if dentist.clinic_id == normalized_clinic_id
    ]
    if not tenant_dentists:
        return []

    dentist_by_id = {dentist.id: dentist for dentist in tenant_dentists}
    response = (
        (client or get_supabase_client())
        .table("dentist_services")
        .select("dentist_id")
        .eq("service_id", normalized_service_id)
        .in_("dentist_id", list(dentist_by_id))
        .execute()
    )
    eligible_ids = {str(row["dentist_id"]) for row in (response.data or [])}
    return [
        dentist for dentist in tenant_dentists if dentist.id in eligible_ids
    ]
