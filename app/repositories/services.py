from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from supabase import Client

from app.database.supabase import get_supabase_client
from app.operations.models import Service


_SERVICE_ALIASES = {
    "whitening": "in clinic teeth whitening",
    "teeth whitening": "in clinic teeth whitening",
    "in clinic whitening": "in clinic teeth whitening",
    "cleaning": "scaling and polishing",
    "dental cleaning": "scaling and polishing",
    "scaling": "scaling and polishing",
    "check up": "consultation and check up",
    "checkup": "consultation and check up",
    "consultation": "consultation and check up",
    "dental consultation": "consultation and check up",
    "root canal": "root canal treatment",
    "rct": "root canal treatment",
    "invisalign": "invisalign consultation and 3d scan",
    "invisalign consultation": "invisalign consultation and 3d scan",
    "3d scan": "invisalign consultation and 3d scan",
    "implant": "dental implant consultation",
    "implant consultation": "dental implant consultation",
    "dental implant": "dental implant consultation",
    "filling": "composite filling",
    "emergency": "emergency dental visit",
    "emergency visit": "emergency dental visit",
    "dental emergency": "emergency dental visit",
}


def _normalize_service(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def list_active_services(
    clinic_id: str,
    *,
    client: Client | None = None,
) -> list[Service]:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    response = (
        (client or get_supabase_client())
        .table("services")
        .select("id,clinic_id,name,duration_minutes")
        .eq("clinic_id", normalized_clinic_id)
        .eq("active", True)
        .execute()
    )
    return [Service.model_validate(row) for row in (response.data or [])]


def match_services(services: list[Service], name_text: str) -> list[Service]:
    requested = _normalize_service(name_text)
    exact_matches = [
        service
        for service in services
        if _normalize_service(service.name) == requested
    ]
    if exact_matches:
        return exact_matches

    canonical = _SERVICE_ALIASES.get(requested)
    if canonical is None:
        return []
    return [
        service
        for service in services
        if _normalize_service(service.name) == canonical
    ]
