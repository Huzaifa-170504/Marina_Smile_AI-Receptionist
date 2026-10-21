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


def filter_services_for_dentist(
    clinic_id: str,
    services: list[Service],
    dentist_id: str,
    *,
    client: Client | None = None,
) -> list[Service]:
    normalized_clinic_id = str(UUID(str(clinic_id)))
    normalized_dentist_id = str(UUID(str(dentist_id)))
    tenant_services = [
        service for service in services if service.clinic_id == normalized_clinic_id
    ]
    if not tenant_services:
        return []

    service_by_id = {service.id: service for service in tenant_services}
    response = (
        (client or get_supabase_client())
        .table("dentist_services")
        .select("service_id")
        .eq("clinic_id", normalized_clinic_id)
        .eq("dentist_id", normalized_dentist_id)
        .in_("service_id", list(service_by_id))
        .execute()
    )
    assigned_ids = {str(row["service_id"]) for row in (response.data or [])}
    return [
        service for service in tenant_services if service.id in assigned_ids
    ]


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


def find_services_in_text(services: list[Service], text: str) -> list[Service]:
    """Find controlled active service names or aliases inside free text."""
    normalized_text = _normalize_service(text)
    if not normalized_text:
        return []

    padded_text = f" {normalized_text} "
    matches: list[Service] = []
    for service in services:
        normalized_name = _normalize_service(service.name)
        if f" {normalized_name} " in padded_text:
            matches.append(service)

    for alias, canonical in _SERVICE_ALIASES.items():
        if f" {alias} " not in padded_text:
            continue
        for service in services:
            if (
                _normalize_service(service.name) == canonical
                and service not in matches
            ):
                matches.append(service)
    return matches
