from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.operations.models import BusyInterval, DentistSchedule


def intervals_overlap(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    return first_start < second_end and first_end > second_start


def _ceil_to_interval(value: datetime, interval_minutes: int) -> datetime:
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((value - midnight).total_seconds() // 60)
    aligned_minutes = (
        (elapsed_minutes + interval_minutes - 1) // interval_minutes
    ) * interval_minutes
    return midnight + timedelta(minutes=aligned_minutes)


def _dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def generate_candidate_starts(
    schedules: list[DentistSchedule],
    *,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    interval_minutes: int,
    timezone: ZoneInfo,
) -> list[datetime]:
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("availability windows must be timezone-aware")
    if window_end <= window_start:
        return []
    if duration_minutes <= 0 or interval_minutes <= 0:
        raise ValueError("duration and interval must be greater than zero")

    starts: set[datetime] = set()
    local_start = window_start.astimezone(timezone)
    local_end = window_end.astimezone(timezone)
    for target_date in _dates_between(local_start.date(), local_end.date()):
        for schedule in schedules:
            if schedule.weekday != target_date.weekday():
                continue
            schedule_start = datetime.combine(
                target_date,
                schedule.start_time,
                tzinfo=timezone,
            )
            schedule_end = datetime.combine(
                target_date,
                schedule.end_time,
                tzinfo=timezone,
            )
            if schedule_end <= schedule_start:
                continue
            candidate = _ceil_to_interval(
                max(schedule_start, local_start),
                interval_minutes,
            )
            latest_end = min(schedule_end, local_end)
            while candidate + timedelta(minutes=duration_minutes) <= latest_end:
                starts.add(candidate)
                candidate += timedelta(minutes=interval_minutes)
    return sorted(starts)


def generate_candidate_starts_in_interval(
    interval_start: datetime,
    interval_end: datetime,
    *,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    interval_minutes: int,
    timezone: ZoneInfo,
) -> list[datetime]:
    """Generate aligned slots inside one date-specific availability interval."""
    values = (interval_start, interval_end, window_start, window_end)
    if any(value.tzinfo is None for value in values):
        raise ValueError("availability intervals must be timezone-aware")
    if duration_minutes <= 0 or interval_minutes <= 0:
        raise ValueError("duration and interval must be greater than zero")

    local_interval_start = interval_start.astimezone(timezone)
    local_interval_end = interval_end.astimezone(timezone)
    local_window_start = window_start.astimezone(timezone)
    local_window_end = window_end.astimezone(timezone)
    if local_interval_end <= local_interval_start or local_window_end <= local_window_start:
        return []

    starts: set[datetime] = set()
    for target_date in _dates_between(
        local_interval_start.date(), local_interval_end.date()
    ):
        day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone)
        day_end = day_start + timedelta(days=1)
        segment_start = max(
            local_interval_start,
            local_window_start,
            day_start,
        )
        segment_end = min(local_interval_end, local_window_end, day_end)
        candidate = _ceil_to_interval(segment_start, interval_minutes)
        while candidate + timedelta(minutes=duration_minutes) <= segment_end:
            starts.add(candidate)
            candidate += timedelta(minutes=interval_minutes)
    return sorted(starts)


def remove_conflicting_starts(
    starts: list[datetime],
    *,
    duration_minutes: int,
    busy_intervals: list[BusyInterval],
) -> list[datetime]:
    duration = timedelta(minutes=duration_minutes)
    return [
        start
        for start in starts
        if not any(
            intervals_overlap(start, start + duration, busy.start_at, busy.end_at)
            for busy in busy_intervals
        )
    ]
