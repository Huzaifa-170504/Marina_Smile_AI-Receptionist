from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.operations.date_parser import local_now
from app.operations.models import AvailabilitySearchResult, AvailabilityStatus


def _doctor_name(name: str) -> str:
    return name if name.casefold().startswith("dr") else f"Dr. {name}"


def _format_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _format_day(value: datetime, current: datetime) -> str:
    if value.date() == current.date():
        return "today"
    if value.date() == (current + timedelta(days=1)).date():
        return "tomorrow"
    return value.strftime("%A")


def format_availability_response(
    result: AvailabilitySearchResult,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> str:
    if result.status == AvailabilityStatus.NEEDS_CLARIFICATION:
        return result.reason or "Which day and time would you prefer?"
    if result.status == AvailabilityStatus.DENTIST_NOT_FOUND:
        return result.reason or "I couldn't find that dentist."
    if result.status == AvailabilityStatus.SERVICE_NOT_FOUND:
        return result.reason or "I couldn't find that service."
    if result.status == AvailabilityStatus.CALENDAR_UNAVAILABLE:
        return "I can't confirm availability right now. Please try again shortly."
    if result.status == AvailabilityStatus.UNSUPPORTED_INTENT:
        return "That appointment action isn't available yet."
    if result.status != AvailabilityStatus.AVAILABLE or not result.slots:
        return "I couldn't find an available slot in that time window."

    current = local_now(timezone_name, now)
    slots = sorted(result.slots, key=lambda slot: slot.start_at)
    if len(slots) == 1:
        slot = slots[0]
        return (
            f"{_doctor_name(slot.dentist_name)} is available "
            f"{_format_day(slot.start_at, current)} at {_format_time(slot.start_at)}."
        )

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for slot in slots:
        day = _format_day(slot.start_at, current)
        grouped[(day, slot.dentist_name)].append(_format_time(slot.start_at))

    days = {day for day, _dentist in grouped}
    dentists = {dentist for _day, dentist in grouped}
    if len(days) == 1 and len(dentists) == 1:
        (day, dentist), times = next(iter(grouped.items()))
        joined = ", ".join(times[:-1]) + f", and {times[-1]}"
        return f"{_doctor_name(dentist)} has these available times {day}: {joined}."

    lines = []
    for (day, dentist), times in grouped.items():
        lines.append(f"• {_doctor_name(dentist)} ({day}): {', '.join(times)}")
    return "Available times:\n" + "\n".join(lines)
