from dataclasses import dataclass
from typing import Any
from uuid import UUID

from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.rag.embeddings import embed_query


@dataclass(frozen=True)
class KnowledgeResult:
    filename: str
    page_number: int | None
    section: str | None
    content: str
    similarity: float


def _to_result(row: dict[str, Any]) -> KnowledgeResult:
    metadata = row.get("metadata") or {}
    filename = row.get("filename") or metadata.get("source_filename") or "unknown"
    section = row.get("section") or metadata.get("section")
    page_number = row.get("page_number")
    return KnowledgeResult(
        filename=str(filename),
        page_number=int(page_number) if page_number is not None else None,
        section=str(section) if section is not None else None,
        content=str(row.get("content") or ""),
        similarity=float(row.get("similarity") or 0.0),
    )


def search_knowledge(
    clinic_id: str,
    query: str,
    match_count: int | None = None,
    match_threshold: float | None = None,
    *,
    client: Client | None = None,
) -> list[KnowledgeResult]:
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
    except ValueError as exc:
        raise ValueError("clinic_id must be a valid UUID") from exc

    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")

    settings = get_settings()
    result_count = settings.rag_match_count if match_count is None else match_count
    threshold = (
        settings.rag_match_threshold
        if match_threshold is None
        else match_threshold
    )
    if result_count <= 0:
        raise ValueError("match_count must be greater than zero")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("match_threshold must be between 0 and 1")

    response = (client or get_supabase_client()).rpc(
        "match_knowledge_chunks",
        {
            "p_clinic_id": normalized_clinic_id,
            "p_query_embedding": embed_query(query),
            "p_match_threshold": threshold,
            "p_match_count": result_count,
        },
    ).execute()
    return [_to_result(row) for row in (response.data or [])]
