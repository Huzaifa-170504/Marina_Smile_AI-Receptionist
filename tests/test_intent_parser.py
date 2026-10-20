from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from google.genai import types

from app.operations import intent_parser
from app.operations.date_parser import resolve_request_window
from app.operations.intent_parser import parse_operational_request
from app.operations.models import OperationalIntent, TimePeriod


NOW = datetime(2026, 10, 13, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))


@pytest.mark.parametrize(
    "message",
    [
        "Can Sara see me tomorrow at 6?",
        "Is Dr. Sara free at six tomorrow?",
        "I want to visit Sara tomorrow evening around 6.",
        "Any chance Sara has time tomorrow at 18:00?",
    ],
)
def test_flexible_sara_requests_resolve_to_same_exact_time(message: str) -> None:
    parsed = parse_operational_request(message)
    resolved = resolve_request_window(
        parsed,
        timezone_name="Asia/Dubai",
        now=NOW,
    )

    assert parsed.intent == OperationalIntent.CHECK_AVAILABILITY
    assert parsed.dentist_text == "Sara"
    assert resolved.requested_start == datetime(
        2026,
        10,
        14,
        18,
        0,
        tzinfo=ZoneInfo("Asia/Dubai"),
    )


@pytest.mark.parametrize(
    ("message", "expected_period", "expected_after"),
    [
        ("I need whitening after four tomorrow.", None, 16),
        ("Any whitening slots tomorrow evening?", TimePeriod.EVENING, None),
        ("Who can do whitening tomorrow?", None, None),
    ],
)
def test_service_requests_extract_whitening_and_any_dentist(
    message: str,
    expected_period: TimePeriod | None,
    expected_after: int | None,
) -> None:
    parsed = parse_operational_request(message)

    assert parsed.intent == OperationalIntent.FIND_SLOTS
    assert parsed.service_text == "whitening"
    assert parsed.any_dentist is True
    assert parsed.period == expected_period
    assert (parsed.after_time.hour if parsed.after_time else None) == expected_after


def test_missing_date_needs_clarification() -> None:
    parsed = parse_operational_request("Do you have anyone free?")

    assert parsed.intent == OperationalIntent.FIND_SLOTS
    assert parsed.needs_clarification is True
    assert parsed.clarification_reason == "Which day would you prefer?"


@pytest.mark.parametrize(
    ("message", "service", "earliest"),
    [
        ("What's the earliest Invisalign appointment?", "Invisalign", True),
        ("Any slot for whitening this weekend?", "whitening", False),
        (
            "Is there someone available for a root canal in the evening?",
            "root canal",
            False,
        ),
    ],
)
def test_additional_flexible_service_phrases(
    message: str,
    service: str,
    earliest: bool,
) -> None:
    parsed = parse_operational_request(message)

    assert parsed.intent == OperationalIntent.FIND_SLOTS
    assert parsed.service_text == service
    assert parsed.wants_earliest is earliest
    assert parsed.any_dentist is True


def test_booking_intent_preserves_structured_selection_details() -> None:
    parsed = parse_operational_request(
        "I want to book the 4:30 whitening slot with Sara tomorrow."
    )

    assert parsed.intent == OperationalIntent.BOOK
    assert parsed.dentist_text == "Sara"
    assert parsed.service_text == "whitening"
    assert parsed.requested_date_text == "tomorrow"
    assert parsed.requested_time_text == "4:30"


def test_gemini_fallback_is_small_structured_and_language_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Models:
        def generate_content(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                parsed={
                    "intent": "FIND_SLOTS",
                    "dentist": "Sara",
                    "service": None,
                    "date": "tomorrow",
                    "time": None,
                    "period": "afternoon",
                    "earliest": False,
                    "any_dentist": False,
                }
            )

    monkeypatch.setattr(
        intent_parser,
        "get_settings",
        lambda: SimpleNamespace(gemini_model="gemini-test"),
    )
    monkeypatch.setattr(
        intent_parser,
        "get_gemini_client",
        lambda: SimpleNamespace(models=Models()),
    )

    parsed = parse_operational_request(
        "Could you arrange something with Sara later?"
    )

    assert parsed.intent == OperationalIntent.FIND_SLOTS
    assert parsed.dentist_text == "Sara"
    config = calls[0]["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.temperature == 0
    assert config.max_output_tokens == 128
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert "APPROVED CLINIC CONTEXT" not in str(calls[0]["contents"])


def test_separate_requests_do_not_share_state() -> None:
    messages = [
        "Can Sara see me tomorrow at 6 PM?",
        "Anything free with Omar Tuesday afternoon?",
        "Who can do whitening tomorrow?",
    ]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(parse_operational_request, messages))

    assert results[0].dentist_text == "Sara"
    assert results[0].requested_time_text == "at 6 PM"
    assert results[1].dentist_text == "Omar"
    assert results[1].period == TimePeriod.AFTERNOON
    assert results[2].dentist_text is None
    assert results[2].service_text == "whitening"
    assert results[2].any_dentist is True


@pytest.mark.parametrize(
    ("message", "date_text", "time_text", "period", "next_available"),
    [
        ("Move my appointment to 6 tomorrow.", "tomorrow", "6", None, False),
        ("Can I come tomorrow instead?", "tomorrow", None, None, False),
        ("Change my whitening booking to Friday.", "Friday", None, None, False),
        ("Move my appointment with Sara to the evening tomorrow.", "tomorrow", None, TimePeriod.EVENING, False),
        ("I need a later time.", None, None, None, False),
        ("What's the next available slot for my appointment?", None, None, None, True),
    ],
)
def test_reschedule_language_preserves_operational_details(
    message: str,
    date_text: str | None,
    time_text: str | None,
    period: TimePeriod | None,
    next_available: bool,
) -> None:
    parsed = parse_operational_request(message)

    assert parsed.intent == OperationalIntent.RESCHEDULE
    assert parsed.requested_date_text == date_text
    assert parsed.period == period
    assert parsed.wants_next_available is next_available
    if time_text is not None:
        assert parsed.requested_time_text is not None
        assert time_text in parsed.requested_time_text


@pytest.mark.parametrize(
    "message",
    [
        "Cancel my appointment.",
        "I can't make it tomorrow.",
        "Please cancel the whitening booking.",
        "I don't need the appointment anymore.",
        "Cancel my appointment with Sara.",
    ],
)
def test_cancellation_language_is_structured_without_execution(message: str) -> None:
    parsed = parse_operational_request(message)

    assert parsed.intent == OperationalIntent.CANCEL
    assert parsed.needs_clarification is False


def test_incomplete_reschedule_requests_target_time_clarification() -> None:
    parsed = parse_operational_request("Move my appointment.")

    assert parsed.intent == OperationalIntent.RESCHEDULE
    assert parsed.needs_clarification is True
    assert parsed.clarification_reason == "What day or time would you prefer?"


def test_change_requests_extract_filters_without_inventing_service_changes() -> None:
    reschedule_request = parse_operational_request(
        "Change my whitening booking to Friday."
    )
    later_request = parse_operational_request("I need a later time.")
    cancellation_request = parse_operational_request(
        "Please cancel my whitening booking with Sara tomorrow."
    )

    assert reschedule_request.service_text == "whitening"
    assert later_request.service_text is None
    assert cancellation_request.service_text == "whitening"
    assert cancellation_request.dentist_text == "Sara"
    assert cancellation_request.requested_date_text == "tomorrow"


def test_explicit_appointment_reference_is_extracted() -> None:
    appointment_id = "8c044310-fa1d-49d2-87b3-e6b90f9970c7"

    parsed = parse_operational_request(f"Cancel appointment {appointment_id}.")

    assert parsed.intent == OperationalIntent.CANCEL
    assert parsed.appointment_id == appointment_id
