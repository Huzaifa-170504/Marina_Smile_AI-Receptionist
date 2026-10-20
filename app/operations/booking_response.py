from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.operations.booking_models import BookingResult, BookingStatus


def format_booking_response(
    result: BookingResult,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> str:
    if result.status == BookingStatus.CONFIRMED:
        if not result.start_at or not result.dentist_name or not result.service_name:
            return "Your appointment is confirmed."
        timezone = ZoneInfo(timezone_name)
        local_start = result.start_at.astimezone(timezone)
        current = (now or datetime.now(timezone)).astimezone(timezone)
        day = (
            "today"
            if local_start.date() == current.date()
            else "tomorrow"
            if local_start.date() == current.date() + timedelta(days=1)
            else local_start.strftime("%A")
        )
        service = result.service_name.casefold()
        return (
            f"Your {service} appointment with Dr. "
            f"{_without_title(result.dentist_name)} is confirmed for "
            f"{day} at {local_start.strftime('%I:%M %p').lstrip('0')}."
        )
    if result.status == BookingStatus.SLOT_TAKEN:
        return "That slot was just taken. Please choose another available time."
    if result.status == BookingStatus.NEEDS_SERVICE:
        return "Which treatment would you like to book?"
    if result.status in {BookingStatus.CALENDAR_ERROR, BookingStatus.CONSISTENCY_ERROR}:
        return (
            "I couldn't complete the booking right now. Please try another slot "
            "or contact reception."
        )
    if result.status == BookingStatus.IN_PROGRESS:
        return "That booking request is already being processed."
    return result.reason or "I couldn't complete the booking. Please contact reception."


def _without_title(name: str) -> str:
    normalized = name.strip()
    if normalized.casefold().startswith("dr. "):
        return normalized[4:]
    if normalized.casefold().startswith("dr "):
        return normalized[3:]
    return normalized
