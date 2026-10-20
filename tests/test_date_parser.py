from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.operations.date_parser import (
    parse_clock_time,
    parse_date_range,
    resolve_request_window,
)
from app.operations.models import OperationalIntent, ParsedOperationalRequest, TimePeriod


TIMEZONE = ZoneInfo("Asia/Dubai")
NOW = datetime(2026, 10, 13, 10, 7, tzinfo=TIMEZONE)


def test_clock_parser_handles_words_24_hour_and_bare_evening_hour() -> None:
    assert parse_clock_time("six") == time(18, 0)
    assert parse_clock_time("18:30") == time(18, 30)
    assert parse_clock_time("9 AM") == time(9, 0)


def test_relative_dates_use_clinic_local_now() -> None:
    assert parse_date_range("tomorrow", now=NOW) == (
        datetime(2026, 10, 14).date(),
        datetime(2026, 10, 14).date(),
    )
    assert parse_date_range("next Tuesday", now=NOW) == (
        datetime(2026, 10, 20).date(),
        datetime(2026, 10, 20).date(),
    )


def test_period_and_bounds_create_timezone_aware_window() -> None:
    request = ParsedOperationalRequest(
        intent=OperationalIntent.FIND_SLOTS,
        requested_date_text="tomorrow",
        period=TimePeriod.EVENING,
        after_time=time(18, 0),
    )

    resolved = resolve_request_window(
        request,
        timezone_name="Asia/Dubai",
        now=NOW,
    )

    assert resolved.search_window_start == datetime(2026, 10, 14, 18, 0, tzinfo=TIMEZONE)
    assert resolved.search_window_end == datetime(2026, 10, 14, 21, 0, tzinfo=TIMEZONE)


def test_around_time_creates_one_hour_window_each_side() -> None:
    request = ParsedOperationalRequest(
        intent=OperationalIntent.FIND_SLOTS,
        requested_date_text="tomorrow",
        requested_time_text="around 5",
    )

    resolved = resolve_request_window(
        request,
        timezone_name="Asia/Dubai",
        now=NOW,
    )

    assert resolved.requested_start == datetime(2026, 10, 14, 17, 0, tzinfo=TIMEZONE)
    assert resolved.search_window_start == datetime(2026, 10, 14, 16, 0, tzinfo=TIMEZONE)
    assert resolved.search_window_end == datetime(2026, 10, 14, 18, 0, tzinfo=TIMEZONE)


def test_earliest_search_is_bounded_and_interval_aligned() -> None:
    request = ParsedOperationalRequest(
        intent=OperationalIntent.FIND_SLOTS,
        wants_earliest=True,
    )

    resolved = resolve_request_window(
        request,
        timezone_name="Asia/Dubai",
        now=NOW,
        slot_interval_minutes=15,
        earliest_search_days=30,
    )

    assert resolved.search_window_start == datetime(2026, 10, 13, 10, 15, tzinfo=TIMEZONE)
    assert resolved.search_window_end == datetime(2026, 11, 12, 21, 0, tzinfo=TIMEZONE)
