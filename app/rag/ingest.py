from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pypdf import PdfReader
from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.rag.embeddings import embed_document


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageText:
    page_number: int
    content: str


@dataclass(frozen=True)
class KnowledgeChunk:
    page_number: int
    chunk_index: int
    content: str


@dataclass(frozen=True)
class IngestionResult:
    filename: str
    page_count: int
    chunk_count: int
    document_id: str
    embedding_dimensions: int
    status: str
    already_ingested: bool = False


def calculate_checksum(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as pdf_file:
        for block in iter(lambda: pdf_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\t\f\v ]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def extract_pdf_pages(pdf_path: Path) -> tuple[int, list[PageText]]:
    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for page_number, page in enumerate(reader.pages, start=1):
        content = normalize_text(page.extract_text() or "")
        if content:
            pages.append(PageText(page_number=page_number, content=content))
    return len(reader.pages), pages


def _find_chunk_end(text: str, start: int, chunk_size: int) -> int:
    proposed_end = min(start + chunk_size, len(text))
    if proposed_end == len(text) or text[proposed_end].isspace():
        return proposed_end

    word_boundary = text.rfind(" ", start, proposed_end + 1)
    newline_boundary = text.rfind("\n", start, proposed_end + 1)
    boundary = max(word_boundary, newline_boundary)
    if boundary > start:
        return boundary

    next_space = re.search(r"\s", text[proposed_end:])
    return proposed_end + next_space.start() if next_space else len(text)


def chunk_pages(
    pages: list[PageText],
    chunk_size: int,
    chunk_overlap: int,
) -> list[KnowledgeChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    chunks: list[KnowledgeChunk] = []
    chunk_index = 0
    for page in pages:
        start = 0
        while start < len(page.content):
            end = _find_chunk_end(page.content, start, chunk_size)
            content = page.content[start:end].strip()
            if content:
                chunks.append(
                    KnowledgeChunk(
                        page_number=page.page_number,
                        chunk_index=chunk_index,
                        content=content,
                    )
                )
                chunk_index += 1

            if end >= len(page.content):
                break

            next_start = max(start + 1, end - chunk_overlap)
            if next_start > start and not page.content[next_start - 1].isspace():
                previous_boundary = max(
                    page.content.rfind(" ", start, next_start),
                    page.content.rfind("\n", start, next_start),
                )
                if previous_boundary > start:
                    next_start = previous_boundary + 1
            while next_start < end and page.content[next_start].isspace():
                next_start += 1
            start = next_start if next_start > start else end

    return chunks


def _validate_inputs(clinic_id: str, pdf_path: str | Path) -> tuple[str, Path]:
    try:
        normalized_clinic_id = str(UUID(str(clinic_id)))
    except ValueError as exc:
        raise ValueError("clinic_id must be a valid UUID") from exc

    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("pdf_path must point to a PDF file")
    return normalized_clinic_id, path


def _find_existing_document(
    client: Client,
    clinic_id: str,
    checksum: str,
) -> dict[str, Any] | None:
    response = (
        client.table("knowledge_documents")
        .select("id,status")
        .eq("clinic_id", clinic_id)
        .eq("checksum", checksum)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def ingest_pdf(
    clinic_id: str,
    pdf_path: str | Path,
    *,
    client: Client | None = None,
) -> IngestionResult:
    normalized_clinic_id, path = _validate_inputs(clinic_id, pdf_path)
    settings = get_settings()
    supabase = client or get_supabase_client()
    checksum = calculate_checksum(path)

    existing_document = _find_existing_document(
        supabase,
        normalized_clinic_id,
        checksum,
    )
    if existing_document and existing_document["status"] == "ready":
        page_count, pages = extract_pdf_pages(path)
        chunks = chunk_pages(
            pages,
            settings.rag_chunk_size,
            settings.rag_chunk_overlap,
        )
        return IngestionResult(
            filename=path.name,
            page_count=page_count,
            chunk_count=len(chunks),
            document_id=str(existing_document["id"]),
            embedding_dimensions=settings.gemini_embedding_dimensions,
            status="ready",
            already_ingested=True,
        )

    if existing_document and existing_document["status"] == "processing":
        raise RuntimeError("Document is already being processed")

    if existing_document and existing_document["status"] == "failed":
        document_id = str(existing_document["id"])
        (
            supabase.table("knowledge_chunks")
            .delete()
            .eq("document_id", document_id)
            .execute()
        )
        (
            supabase.table("knowledge_documents")
            .update({"status": "processing"})
            .eq("id", document_id)
            .execute()
        )
    elif existing_document:
        raise RuntimeError(
            f"Document has unsupported ingestion status: {existing_document['status']!r}"
        )
    else:
        document_response = (
            supabase.table("knowledge_documents")
            .insert(
                {
                    "clinic_id": normalized_clinic_id,
                    "filename": path.name,
                    "storage_path": str(path),
                    "mime_type": "application/pdf",
                    "checksum": checksum,
                    "version": 1,
                    "status": "processing",
                }
            )
            .execute()
        )
        if not document_response.data or "id" not in document_response.data[0]:
            raise RuntimeError("Supabase did not return the created document ID")
        document_id = str(document_response.data[0]["id"])

    try:
        page_count, pages = extract_pdf_pages(path)
        chunks = chunk_pages(
            pages,
            settings.rag_chunk_size,
            settings.rag_chunk_overlap,
        )
        if not chunks:
            raise ValueError("The PDF contains no extractable text")

        chunk_records = [
            {
                "clinic_id": normalized_clinic_id,
                "document_id": document_id,
                "content": chunk.content,
                "embedding": embed_document(chunk.content),
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "metadata": {"source_filename": path.name},
            }
            for chunk in chunks
        ]
        supabase.table("knowledge_chunks").insert(chunk_records).execute()
        (
            supabase.table("knowledge_documents")
            .update({"status": "ready"})
            .eq("id", document_id)
            .execute()
        )
    except Exception:
        logger.exception("PDF ingestion failed for document_id=%s", document_id)
        try:
            (
                supabase.table("knowledge_documents")
                .update({"status": "failed"})
                .eq("id", document_id)
                .execute()
            )
        except Exception:
            logger.exception(
                "Could not mark failed ingestion for document_id=%s",
                document_id,
            )
        raise

    return IngestionResult(
        filename=path.name,
        page_count=page_count,
        chunk_count=len(chunks),
        document_id=document_id,
        embedding_dimensions=settings.gemini_embedding_dimensions,
        status="ready",
    )
