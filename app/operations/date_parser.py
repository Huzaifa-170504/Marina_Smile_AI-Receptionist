from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.operations.models import ParsedOperationalRequest, TimePeriod


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_PERIOD_BOUNDS = {
    TimePeriod.MORNING: (time(9, 0), time(12, 0)),
    TimePeriod.AFTERNOON: (time(12, 0), time(17, 0)),
    TimePeriod.EVENING: (time(17, 0), time(21, 0)),
    TimePeriod.ANYTIME: (time(9, 0), time(21, 0)),
}


def get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown clinic timezone: {timezone_name}") from exc


def local_now(timezone_name: str, now: datetime | None = None) -> datetime:
    timezone = get_timezone(timezone_name)
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone)


def parse_clock_time(value: str) -> time | None:
    normalized = value.strip().casefold().replace(".", "")
    word_match = re.search(
        r"\b(" + "|".join(_NUMBER_WORDS) + r")\b(?:\s*(am|pm))?",
        normalized,
    )
    numeric_match = re.search(
        r"(?<!\d)([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?(?!\d)",
        normalized,
    )
    if word_match:
        hour = _NUMBER_WORDS[word_match.group(1)]
        minute = 0
        meridiem = word_match.group(2)
    elif numeric_match:
        hour = int(numeric_match.group(1))
        minute = int(numeric_match.group(2) or 0)
        meridiem = numeric_match.group(3)
    else:
        return None

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem is None and 1 <= hour <= 8:
        hour += 12
    return time(hour, minute)


def parse_date_range(
    value: str,
    *,
    now: datetime,
) -> tuple[date, date] | None:
    normalized = value.strip().casefold()
    today = now.date()
    if normalized == "today":
        return today, today
    if normalized == "tomorrow":
        target = today + timedelta(days=1)
        return target, target
    if "weekend" in normalized:
        days_until_saturday = (5 - today.weekday()) % 7
        saturday = today + timedelta(days=days_until_saturday)
        return saturday, saturday + timedelta(days=1)

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", normalized)
    if iso_match:
        target = date.fromisoformat(iso_match.group(1))
        return target, target

    weekday_match = re.search(
        r"\b(next\s+)?(" + "|".join(_WEEKDAYS) + r")\b",
        normalized,
    )
    if not weekday_match:
        return None
    target_weekday = _WEEKDAYS[weekday_match.group(2)]
    days_ahead = (target_weekday - today.weekday()) % 7
    if weekday_match.group(1):
        days_ahead += 7
    target = today + timedelta(days=days_ahead)
    return target, target


def _combine(target_date: date, value: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(target_date, value, tzinfo=timezone)


def _ceil_to_interval(value: datetime, interval_minutes: int) -> datetime:
    remainder = value.minute % interval_minutes
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return value
    minutes = interval_minutes - remainder if remainder else interval_minutes
    return (value + timedelta(minutes=minutes)).replace(second=0, microsecond=0)


def resolve_request_window(
    request: ParsedOperationalRequest,
    *,
    timezone_name: str,
    now: datetime | None = None,
    slot_interval_minutes: int = 15,
    earliest_search_days: int = 30,
) -> ParsedOperationalRequest:
    current = local_now(timezone_name, now)
    timezone = get_timezone(timezone_name)

    if request.wants_earliest and not request.requested_date_text:
        start = _ceil_to_interval(current, slot_interval_minutes)
        end = _combine(
            current.date() + timedelta(days=earliest_search_days),
            time(21, 0),
            timezone,
        )
        return request.model_copy(
            update={"search_window_start": start, "search_window_end": end}
        )

    if not request.requested_date_text:
        return request.model_copy(
            update={
                "needs_clarification": True,
                "clarification_reason": "Which day would you prefer?",
            }
        )

    date_range = parse_date_range(request.requested_date_text, now=current)
    if date_range is None:
        return request.model_copy(
            update={
                "needs_clarification": True,
                "clarification_reason": "Which day would you prefer?",
            }
        )
    first_date, last_date = date_range

    exact_time = (
        parse_clock_time(request.requested_time_text)
        if request.requested_time_text
        else None
    )
    if exact_time is not None:
        requested_start = _combine(first_date, exact_time, timezone)
        is_around = "around" in request.requested_time_text.casefold()
        if is_around:
            window_start = requested_start - timedelta(hours=1)
            window_end = requested_start + timedelta(hours=1)
        else:
            window_start = requested_start
            window_end = requested_start + timedelta(minutes=slot_interval_minutes)
    else:
        period_start, period_end = _PERIOD_BOUNDS.get(
            request.period or TimePeriod.ANYTIME,
            _PERIOD_BOUNDS[TimePeriod.ANYTIME],
        )
        window_start = _combine(first_date, request.after_time or period_start, timezone)
        window_end = _combine(last_date, request.before_time or period_end, timezone)
        requested_start = None

    if window_end <= current:
        return request.model_copy(
            update={
                "needs_clarification": True,
                "clarification_reason": "Please choose a future date and time.",
            }
        )
    window_start = max(window_start, _ceil_to_interval(current, slot_interval_minutes))
    return request.model_copy(
        update={
            "requested_start": requested_start,
            "search_window_start": window_start,
            "search_window_end": window_end,
        }
    )
