from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.ai.gemini_client import GeminiGenerationError, generate_grounded_answer
from app.ai.prompts import (
    KNOWLEDGE_SYSTEM_PROMPT,
    SAFE_FALLBACK,
    build_knowledge_prompt,
)
from app.rag.retriever import KnowledgeResult, search_knowledge
from app.services.clinic_directory import answer_clinic_directory_question


logger = logging.getLogger(__name__)

_MEDICATION_ADVICE = re.compile(
    r"\b(?:what|which)\s+(?:medicine|medication|painkiller|antibiotic)\b"
    r"|\b(?:prescribe|dosage|dose)\b",
    re.IGNORECASE,
)
_LIVE_AVAILABILITY = re.compile(
    r"\b(?:appointment|slot)\b.*"
    r"\b(?:available|availability|today|tomorrow|tonight|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm))\b"
    r"|\b(?:available|availability)\b.*\b(?:appointment|slot)\b",
    re.IGNORECASE,
)
_PROMPT_INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system)\s+instructions?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KnowledgeSource:
    filename: str
    page_number: int | None
    similarity: float


@dataclass(frozen=True)
class KnowledgeAnswer:
    text: str
    sources: list[KnowledgeSource]
    status: Literal[
        "answered",
        "needs_clarification",
        "needs_staff_confirmation",
    ] = "answered"
    grounding: Literal["conversation", "supabase", "rag", "safety"] = "rag"


def _to_sources(results: list[KnowledgeResult]) -> list[KnowledgeSource]:
    return [
        KnowledgeSource(
            filename=result.filename,
            page_number=result.page_number,
            similarity=result.similarity,
        )
        for result in results
    ]


def _requires_safe_fallback(question: str) -> bool:
    return any(
        pattern.search(question)
        for pattern in (
            _MEDICATION_ADVICE,
            _LIVE_AVAILABILITY,
            _PROMPT_INJECTION,
        )
    )


def answer_knowledge_question(clinic_id: str, question: str) -> KnowledgeAnswer:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty")

    normalized_question = question.strip()
    directory_answer = answer_clinic_directory_question(clinic_id, normalized_question)
    if directory_answer is not None:
        return KnowledgeAnswer(
            text=directory_answer.text,
            sources=[],
            status=directory_answer.status,
            grounding=directory_answer.grounding,
        )

    results = search_knowledge(clinic_id, normalized_question)
    sources = _to_sources(results)
    if not results or _requires_safe_fallback(normalized_question):
        return KnowledgeAnswer(
            text=SAFE_FALLBACK,
            sources=sources,
            status="needs_staff_confirmation",
            grounding="safety",
        )

    user_prompt = build_knowledge_prompt(normalized_question, results)
    try:
        generated = generate_grounded_answer(
            system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
    except GeminiGenerationError:
        logger.exception("Grounded Gemini answer generation failed")
        return KnowledgeAnswer(
            text=SAFE_FALLBACK,
            sources=sources,
            status="needs_staff_confirmation",
            grounding="safety",
        )

    if not generated.supported:
        return KnowledgeAnswer(
            text=SAFE_FALLBACK,
            sources=sources,
            status="needs_staff_confirmation",
            grounding="safety",
        )
    return KnowledgeAnswer(text=generated.text, sources=sources)
