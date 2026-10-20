from __future__ import annotations

import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.ai.knowledge_answer import answer_knowledge_question
from app.api.models import (
    DemoQueryRequest,
    DemoQueryResponse,
    DemoSlotResponse,
    DemoSourceResponse,
    DemoStatusResponse,
)
from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.slot_finder import check_availability_request
from app.services.demo_access import consume_demo_quota, require_demo_access


router = APIRouter(prefix="/api/demo", tags=["public-demo"])
logger = logging.getLogger(__name__)


def _count(table: str, clinic_id: str, *, status_value: str | None = None) -> int:
    query = (
        get_supabase_client()
        .table(table)
        .select("id", count="exact")
        .eq("clinic_id", clinic_id)
    )
    if table in {"dentists", "services"}:
        query = query.eq("active", True)
    if status_value is not None:
        query = query.eq("status", status_value)
    response = query.execute()
    return int(response.count or 0)


@router.get(
    "/status",
    response_model=DemoStatusResponse,
    dependencies=[Depends(require_demo_access)],
)
async def demo_status() -> DemoStatusResponse:
    settings = get_settings()
    clinic_id = str(settings.default_clinic_id)
    try:
        dentists, services, documents = await run_in_threadpool(
            lambda: (
                _count("dentists", clinic_id),
                _count("services", clinic_id),
                _count("knowledge_documents", clinic_id, status_value="ready"),
            )
        )
    except Exception as exc:
        logger.exception("demo_status_failed", extra={"clinic_id": clinic_id})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live project data is temporarily unavailable.",
        ) from exc
    return DemoStatusResponse(
        knowledge="ready" if documents else "empty",
        active_dentists=dentists,
        active_services=services,
        ready_documents=documents,
    )


@router.post(
    "/query",
    response_model=DemoQueryResponse,
    dependencies=[Depends(require_demo_access)],
)
async def demo_query(
    request: DemoQueryRequest,
    fingerprint_hash: Annotated[str, Header(alias="X-Demo-Visitor")],
) -> DemoQueryResponse:
    started = perf_counter()
    await run_in_threadpool(
        consume_demo_quota,
        fingerprint_hash=fingerprint_hash,
        query_type=request.mode,
    )
    clinic_id = str(get_settings().default_clinic_id)
    try:
        if request.mode == "knowledge":
            result = await run_in_threadpool(
                answer_knowledge_question,
                clinic_id,
                request.query,
            )
            return DemoQueryResponse(
                mode="knowledge",
                status="answered" if result.sources else "needs_staff_confirmation",
                answer=result.text,
                sources=[
                    DemoSourceResponse(
                        filename=source.filename,
                        page_number=source.page_number,
                        similarity=source.similarity,
                    )
                    for source in result.sources
                ],
                latency_ms=round((perf_counter() - started) * 1000),
            )

        result = await run_in_threadpool(
            check_availability_request,
            clinic_id,
            request.query,
            limit=5,
        )
        return DemoQueryResponse(
            mode="availability",
            status=result.status.value,
            answer=result.reason or (
                "These slots were verified against clinic schedules, appointments, and Google Calendar."
                if result.slots
                else "No verified slots matched that request."
            ),
            slots=[
                DemoSlotResponse(
                    dentist_name=slot.dentist_name,
                    service_name=slot.service_name,
                    start_at=slot.start_at,
                    end_at=slot.end_at,
                    duration_minutes=slot.duration_minutes,
                    calendar_verified=slot.calendar_verified,
                )
                for slot in result.slots
            ],
            latency_ms=round((perf_counter() - started) * 1000),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "demo_query_failed",
            extra={"clinic_id": clinic_id, "query_type": request.mode},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The live demo could not complete this query right now.",
        ) from exc
