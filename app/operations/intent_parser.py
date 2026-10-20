from __future__ import annotations

import re

from google.genai import types
from pydantic import BaseModel, ConfigDict

from app.ai.gemini_client import get_gemini_client
from app.core.config import get_settings
from app.operations.date_parser import parse_clock_time
from app.operations.models import (
    OperationalIntent,
    ParsedOperationalRequest,
    TimePeriod,
)


INTENT_EXTRACTION_PROMPT = """Extract dental scheduling intent as structured data only.
Do not answer, decide availability, or invent missing values.
Extract intent, appointment_id, dentist, service, date, time, period, earliest,
next_available, and any_dentist.
Use null when information is missing."""

_WEEKDAY_PATTERN = (
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
)
_DATE_PATTERN = re.compile(
    rf"\b(?:today|tomorrow|(?:next\s+)?(?:{_WEEKDAY_PATTERN})|"
    r"(?:this\s+)?weekend|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_PERIOD_PATTERN = re.compile(
    r"\b(morning|afternoon|evening|anytime)\b",
    re.IGNORECASE,
)
_CLOCK_VALUE = (
    r"(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:am|pm)?"
    r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
)
_TIME_PATTERN = re.compile(
    rf"\b((?:around\s+|at\s+)?(?:{_CLOCK_VALUE}))\b",
    re.IGNORECASE,
)
_AFTER_PATTERN = re.compile(rf"\bafter\s+({_CLOCK_VALUE})\b", re.IGNORECASE)
_BEFORE_PATTERN = re.compile(rf"\bbefore\s+({_CLOCK_VALUE})\b", re.IGNORECASE)
_SCHEDULING_SIGNAL = re.compile(
    r"\b(?:available|availability|free|slot|appointment|see me|visit|"
    r"booking|dentist|earliest|tomorrow|morning|afternoon|evening|"
    r"cancel|reschedule|move|change|later|instead)\b",
    re.IGNORECASE,
)
_ANY_DENTIST = re.compile(
    r"\b(?:anyone|someone|any dentist|a dentist|who can)\b",
    re.IGNORECASE,
)


class IntentParsingError(RuntimeError):
    """Raised when Gemini cannot return a valid operational intent."""


class _IntentExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: OperationalIntent
    appointment_id: str | None = None
    dentist: str | None = None
    service: str | None = None
    date: str | None = None
    time: str | None = None
    period: TimePeriod | None = None
    earliest: bool = False
    next_available: bool = False
    any_dentist: bool = False


def parse_operational_request(message: str) -> ParsedOperationalRequest:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must not be empty")
    normalized_message = message.strip()
    deterministic = _deterministic_fast_path(normalized_message)
    if deterministic is not None:
        return deterministic
    return _extract_with_gemini(normalized_message)


def _deterministic_fast_path(message: str) -> ParsedOperationalRequest | None:
    lower = message.casefold()
    detected_intent = _detect_intent(lower)

    date_match = _DATE_PATTERN.search(message)
    period_match = _PERIOD_PATTERN.search(message)
    after_match = _AFTER_PATTERN.search(message)
    before_match = _BEFORE_PATTERN.search(message)
    time_match = _TIME_PATTERN.search(message)
    wants_earliest = bool(re.search(r"\bearliest\b", lower))
    wants_next_available = bool(
        re.search(r"\bnext\s+available(?:\s+(?:time|slot|appointment))?\b", lower)
    )
    any_dentist = bool(_ANY_DENTIST.search(message))
    scheduling_signal = bool(_SCHEDULING_SIGNAL.search(message))
    if not scheduling_signal:
        return None

    dentist_text = _extract_dentist_text(message)
    service_text = _extract_service_text(message)
    requested_time_text = None
    if time_match and not after_match and not before_match:
        requested_time_text = time_match.group(1).strip()

    has_operational_detail = any(
        (
            date_match,
            period_match,
            requested_time_text,
            after_match,
            before_match,
            wants_earliest,
            wants_next_available,
        )
    )
    if (
        not has_operational_detail
        and not any_dentist
        and detected_intent not in {OperationalIntent.CANCEL, OperationalIntent.RESCHEDULE}
    ):
        return None

    intent = detected_intent or (
        OperationalIntent.CHECK_AVAILABILITY
        if requested_time_text and dentist_text
        else OperationalIntent.FIND_SLOTS
    )
    missing_date = (
        intent
        in {
            OperationalIntent.CHECK_AVAILABILITY,
            OperationalIntent.FIND_SLOTS,
            OperationalIntent.RESCHEDULE,
        }
        and not date_match
        and not wants_earliest
        and not wants_next_available
    )
    appointment_match = re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        message,
        re.IGNORECASE,
    )
    return ParsedOperationalRequest(
        intent=intent,
        appointment_id=appointment_match.group(0) if appointment_match else None,
        dentist_text=dentist_text,
        service_text=service_text,
        any_dentist=any_dentist or (service_text is not None and dentist_text is None),
        requested_date_text=date_match.group(0) if date_match else None,
        requested_time_text=requested_time_text,
        period=TimePeriod(period_match.group(1).casefold()) if period_match else None,
        after_time=parse_clock_time(after_match.group(1)) if after_match else None,
        before_time=parse_clock_time(before_match.group(1)) if before_match else None,
        wants_earliest=wants_earliest,
        wants_next_available=wants_next_available,
        confidence=0.95,
        needs_clarification=missing_date,
        clarification_reason=(
            "What day or time would you prefer?"
            if missing_date and intent == OperationalIntent.RESCHEDULE
            else "Which day would you prefer?" if missing_date else None
        ),
    )


def _detect_intent(message: str) -> OperationalIntent | None:
    if re.search(
        r"\b(?:cancel|call off|(?:do not|don't) need (?:the )?appointment|"
        r"can't make|cannot make)\b",
        message,
    ):
        return OperationalIntent.CANCEL
    if re.search(
        r"\b(?:reschedule|move|change)\b.*\b(?:appointment|booking|time)\b"
        r"|\bcome\b.*\binstead\b"
        r"|\b(?:later time|next available)\b",
        message,
    ):
        return OperationalIntent.RESCHEDULE
    if re.search(r"\b(?:book|reserve|confirm)\b", message):
        return OperationalIntent.BOOK
    return None


def _extract_dentist_text(message: str) -> str | None:
    patterns = (
        r"\bdr\.?\s+([A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*)?)",
        r"\bwith\s+([A-Za-z][A-Za-z'-]*)",
        r"\bcan\s+([A-Za-z][A-Za-z'-]*)\s+see\b",
        r"\bvisit\s+([A-Za-z][A-Za-z'-]*)",
        r"\bchance\s+([A-Za-z][A-Za-z'-]*)\s+has\b",
    )
    trailing_stops = {
        "today",
        "tomorrow",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "morning",
        "afternoon",
        "evening",
        "free",
        "available",
        "around",
        "at",
    }
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match and match.group(1).casefold() not in {"anyone", "someone"}:
            parts = match.group(1).strip().split()
            if len(parts) > 1 and parts[-1].casefold() in trailing_stops:
                parts.pop()
            return " ".join(parts)
    return None


def _extract_service_text(message: str) -> str | None:
    patterns = (
        r"\b(?:need|for|do)\s+(?:a\s+|an\s+)?([A-Za-z][A-Za-z' -]*?)"
        rf"(?=\s+(?:(?:sometime\s+)?(?:after|before|today|tomorrow|{_WEEKDAY_PATTERN}|this|next|at|around)"
        r"|in(?:\s+the)?\s+(?:morning|afternoon|evening))|[?.!,]|$)",
        r"\b([A-Za-z]+(?:\s+and\s+[A-Za-z]+)?)\s+slots?\b",
        r"\b([A-Za-z][A-Za-z'-]*)\s+appointments?\b",
        r"\b(?:my\s+)?([A-Za-z][A-Za-z'-]*)\s+bookings?\b",
    )
    ignored = {
        "appointment",
        "dentist",
        "any",
        "anything",
        "earliest",
        "my",
        "time",
        "later",
        "later time",
    }
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            candidate = " ".join(match.group(1).strip().split())
            if candidate.casefold() not in ignored and len(candidate.split()) <= 4:
                return candidate
    return None


def _extract_with_gemini(message: str) -> ParsedOperationalRequest:
    settings = get_settings()
    try:
        response = get_gemini_client().models.generate_content(
            model=settings.gemini_model,
            contents=f"PATIENT TEXT:\n{message}",
            config=types.GenerateContentConfig(
                system_instruction=INTENT_EXTRACTION_PROMPT,
                temperature=0,
                max_output_tokens=128,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW,
                ),
                response_mime_type="application/json",
                response_schema=_IntentExtraction,
            ),
        )
        if isinstance(response.parsed, _IntentExtraction):
            extracted = response.parsed
        elif response.parsed is not None:
            extracted = _IntentExtraction.model_validate(response.parsed)
        else:
            extracted = _IntentExtraction.model_validate_json(response.text or "")
    except Exception as exc:
        raise IntentParsingError("Could not understand scheduling request") from exc

    after_match = _AFTER_PATTERN.search(extracted.time or "")
    before_match = _BEFORE_PATTERN.search(extracted.time or "")
    requested_time_text = (
        None if after_match or before_match else extracted.time
    )
    missing_date = (
        extracted.intent
        in {
            OperationalIntent.CHECK_AVAILABILITY,
            OperationalIntent.FIND_SLOTS,
            OperationalIntent.RESCHEDULE,
        }
        and not extracted.date
        and not extracted.earliest
        and not extracted.next_available
    )
    return ParsedOperationalRequest(
        intent=extracted.intent,
        appointment_id=extracted.appointment_id,
        dentist_text=extracted.dentist,
        service_text=extracted.service,
        any_dentist=extracted.any_dentist,
        requested_date_text=extracted.date,
        requested_time_text=requested_time_text,
        period=extracted.period,
        after_time=parse_clock_time(after_match.group(1)) if after_match else None,
        before_time=parse_clock_time(before_match.group(1)) if before_match else None,
        wants_earliest=extracted.earliest,
        wants_next_available=extracted.next_available,
        confidence=None,
        needs_clarification=missing_date,
        clarification_reason=(
            "What day or time would you prefer?"
            if missing_date and extracted.intent == OperationalIntent.RESCHEDULE
            else "Which day would you prefer?" if missing_date else None
        ),
    )
