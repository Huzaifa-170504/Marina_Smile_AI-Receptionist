from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from supabase import Client

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.google_calendar import (
    CalendarUnavailableError,
    GoogleCalendarClient,
    SyncTokenExpiredError,
)


logger = logging.getLogger(__name__)

UAE_TIMEZONE = "Asia/Dubai"
SYNC_LEASE_MINUTES = 15
SYNC_PAST_DAYS = 365
SYNC_FUTURE_DAYS = 365 * 3


def _as_bool(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value and value[0])
    return bool(value)


def sync_window(now: datetime | None = None) -> tuple[date, date]:
    current = now or datetime.now(timezone.utc)
    today = current.astimezone(ZoneInfo(UAE_TIMEZONE)).date()
    return today - timedelta(days=SYNC_PAST_DAYS), today + timedelta(days=SYNC_FUTURE_DAYS)


def event_dates(event: dict[str, Any]) -> list[date]:
    start = event.get("start") or {}
    end = event.get("end") or {}

    if start.get("date"):
        first = date.fromisoformat(str(start["date"]))
        last = first
        if end.get("date"):
            end_date = date.fromisoformat(str(end["date"]))
            last = max(first, end_date - timedelta(days=1))
        return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]

    if start.get("dateTime"):
        tz = ZoneInfo(UAE_TIMEZONE)
        first_dt = datetime.fromisoformat(
            str(start["dateTime"]).replace("Z", "+00:00")
        ).astimezone(tz)
        first = first_dt.date()
        last = first
        if end.get("dateTime"):
            end_dt = datetime.fromisoformat(
                str(end["dateTime"]).replace("Z", "+00:00")
            ).astimezone(tz)
            last = max(first, (end_dt - timedelta(seconds=1)).date())
        return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]

    return []


def discover_uae_holiday_calendar(
    calendar: GoogleCalendarClient,
    configured_id: str,
) -> tuple[str, dict[str, Any]]:
    """Use calendarList discovery, then the configured public calendar fallback."""
    for item in calendar.list_calendars():
        summary = str(item.get("summary") or "").casefold()
        calendar_id = str(item.get("id") or "")
        if "holiday" in summary and (
            "united arab emirates" in summary
            or "uae" in summary
            or "ae.official" in calendar_id
        ):
            return calendar_id, item

    if not configured_id.strip():
        raise CalendarUnavailableError(
            "No UAE holiday calendar was discovered. Subscribe the existing Google "
            "account to the UAE holidays calendar or set UAE_HOLIDAY_CALENDAR_ID."
        )

    metadata = calendar.get_calendar(configured_id)
    summary = str(metadata.get("summary") or "").casefold()
    if "holiday" not in summary or "united arab emirates" not in summary:
        raise CalendarUnavailableError(
            "The configured calendar is not the UAE public holidays calendar."
        )
    return configured_id, metadata


def build_holiday_rows(
    clinic_id: str,
    calendar_id: str,
    events: list[dict[str, Any]],
    synced_at: datetime,
) -> tuple[
    list[dict[str, Any]],
    set[str],
    set[str],
    set[tuple[str, date]],
    date | None,
]:
    rows: list[dict[str, Any]] = []
    active_ids: set[str] = set()
    cancelled_ids: set[str] = set()
    valid_pairs: set[tuple[str, date]] = set()
    max_date: date | None = None

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        event_id = str(event_id)
        if event.get("status") == "cancelled":
            cancelled_ids.add(event_id)
            continue
        dates = event_dates(event)
        if not dates:
            continue
        active_ids.add(event_id)
        name = str(event.get("summary") or "Public holiday")
        updated_at = None
        if event.get("updated"):
            try:
                updated_at = datetime.fromisoformat(
                    str(event["updated"]).replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                updated_at = None
        for day in dates:
            valid_pairs.add((event_id, day))
            rows.append(
                {
                    "clinic_id": clinic_id,
                    "holiday_date": day.isoformat(),
                    "name": name,
                    "source": "google_calendar",
                    "google_event_id": event_id,
                    "calendar_id": calendar_id,
                    "last_synced_at": synced_at.isoformat(),
                    "event_updated_at": updated_at,
                }
            )
            if max_date is None or day > max_date:
                max_date = day
    return rows, active_ids, cancelled_ids, valid_pairs, max_date


def stale_row_ids(
    existing: list[dict[str, Any]],
    valid_pairs: set[tuple[str, date]],
    active_ids: set[str],
    cancelled_ids: set[str],
    *,
    full_sync: bool,
) -> list[str]:
    stale: list[str] = []
    for row in existing:
        event_id = str(row.get("google_event_id") or "")
        if not event_id:
            continue
        day = date.fromisoformat(str(row["holiday_date"]))
        if event_id in cancelled_ids:
            stale.append(str(row["id"]))
        elif full_sync and (event_id, day) not in valid_pairs:
            stale.append(str(row["id"]))
        elif not full_sync and event_id in active_ids and (event_id, day) not in valid_pairs:
            stale.append(str(row["id"]))
    return stale


def _chunks(values: list[Any], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _update_sync_state(db: Client, clinic_id: str, values: dict[str, Any]) -> None:
    db.table("clinic_holiday_sync").update(values).eq("clinic_id", clinic_id).execute()


def sync_uae_holidays(
    clinic_id: str | None = None,
    *,
    db: Client | None = None,
    calendar_client: GoogleCalendarClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    supabase = db or get_supabase_client()
    clinic_id = clinic_id or str(get_settings().default_clinic_id)
    current = now or datetime.now(timezone.utc)

    claimed = _as_bool(
        supabase.rpc(
            "claim_holiday_sync",
            {"p_clinic_id": clinic_id, "p_lease_minutes": SYNC_LEASE_MINUTES},
        ).execute().data
    )
    if not claimed:
        return {"status": "skipped", "reason": "another sync is running"}

    try:
        settings = get_settings()
        calendar = calendar_client or GoogleCalendarClient.from_default_credentials(
            timeout_seconds=settings.google_calendar_timeout_seconds,
            credentials_file=settings.google_application_credentials,
        )
        calendar_id, _metadata = discover_uae_holiday_calendar(
            calendar, settings.uae_holiday_calendar_id
        )

        state_rows = (
            supabase.table("clinic_holiday_sync")
            .select("calendar_id,sync_token,last_synced_at")
            .eq("clinic_id", clinic_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        state = state_rows[0] if state_rows else {}
        previous_token = state.get("sync_token")
        previous_calendar = state.get("calendar_id")
        last_synced = state.get("last_synced_at")
        if last_synced:
            try:
                last_synced_at = datetime.fromisoformat(
                    str(last_synced).replace("Z", "+00:00")
                )
                stale_after = current - timedelta(
                    hours=settings.holiday_sync_interval_hours * 2
                )
                if last_synced_at < stale_after:
                    logger.warning(
                        "UAE holiday sync is stale",
                        extra={
                            "clinic_id": clinic_id,
                            "last_synced_at": last_synced_at.isoformat(),
                        },
                    )
            except ValueError:
                logger.warning("Invalid last_synced_at in holiday sync state")

        start_date, end_date = sync_window(current)
        start_at = datetime.combine(
            start_date, time.min, tzinfo=ZoneInfo(UAE_TIMEZONE)
        )
        end_at = datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(UAE_TIMEZONE)
        )

        incremental = bool(previous_token and previous_calendar == calendar_id)
        events: list[dict[str, Any]] = []
        next_token: str | None = None
        if incremental:
            try:
                events, next_token = calendar.list_events(
                    calendar_id,
                    sync_token=previous_token,
                    show_deleted=True,
                    timezone_name=UAE_TIMEZONE,
                )
            except SyncTokenExpiredError:
                logger.info("Holiday sync token expired; performing full sync")
                incremental = False

        if not incremental:
            events, next_token = calendar.list_events(
                calendar_id,
                time_min=start_at,
                time_max=end_at,
                show_deleted=False,
                timezone_name=UAE_TIMEZONE,
            )

        rows, active_ids, cancelled_ids, valid_pairs, coverage_end = build_holiday_rows(
            clinic_id, calendar_id, events, current
        )

        # An empty full response is not proof that every holiday was deleted.
        if not incremental and not events:
            _update_sync_state(
                supabase,
                clinic_id,
                {
                    "calendar_id": calendar_id,
                    "sync_token": next_token or previous_token,
                    "last_synced_at": current.isoformat(),
                    "locked_until": None,
                    "last_error": None,
                    "last_status": "empty",
                    "coverage_end": None,
                },
            )
            logger.warning(
                "UAE holiday calendar returned no events; existing rows preserved",
                extra={"clinic_id": clinic_id, "calendar_id": calendar_id},
            )
            return {
                "status": "empty",
                "calendar_id": calendar_id,
                "events": 0,
                "rows": 0,
                "deleted": 0,
                "coverage_end": None,
                "incremental": False,
            }

        for batch in _chunks(rows):
            supabase.table("clinic_holidays").upsert(
                batch,
                on_conflict="clinic_id,holiday_date",
            ).execute()

        existing = (
            supabase.table("clinic_holidays")
            .select("id,holiday_date,google_event_id")
            .eq("clinic_id", clinic_id)
            .eq("source", "google_calendar")
            .eq("calendar_id", calendar_id)
            .gte("holiday_date", start_date.isoformat())
            .lte("holiday_date", end_date.isoformat())
            .execute()
            .data
            or []
        )
        stale = stale_row_ids(
            existing,
            valid_pairs,
            active_ids,
            cancelled_ids,
            full_sync=not incremental,
        )
        for batch in _chunks(stale):
            supabase.table("clinic_holidays").delete().in_("id", batch).execute()

        _update_sync_state(
            supabase,
            clinic_id,
            {
                "calendar_id": calendar_id,
                "sync_token": next_token,
                "last_synced_at": current.isoformat(),
                "locked_until": None,
                "last_error": None,
                "last_status": "success" if events else "empty",
                "coverage_end": coverage_end.isoformat() if coverage_end else None,
            },
        )
        logger.info(
            "UAE holiday sync completed",
            extra={
                "clinic_id": clinic_id,
                "calendar_id": calendar_id,
                "events": len(events),
                "rows": len(rows),
                "deleted": len(stale),
                "coverage_end": coverage_end.isoformat() if coverage_end else None,
                "incremental": incremental,
            },
        )
        return {
            "status": "success",
            "calendar_id": calendar_id,
            "events": len(events),
            "rows": len(rows),
            "deleted": len(stale),
            "coverage_end": coverage_end.isoformat() if coverage_end else None,
            "incremental": incremental,
        }
    except Exception as exc:
        logger.exception("UAE holiday sync failed", extra={"clinic_id": clinic_id})
        try:
            _update_sync_state(
                supabase,
                clinic_id,
                {
                    "locked_until": None,
                    "last_attempt_at": current.isoformat(),
                    "last_error": str(exc)[:1000],
                    "last_status": "error",
                },
            )
        except Exception:
            logger.exception("Failed to record holiday sync error")
        raise


def sync_configured_clinic() -> dict[str, Any]:
    return sync_uae_holidays(str(get_settings().default_clinic_id))


async def run_holiday_sync_scheduler() -> None:
    interval = get_settings().holiday_sync_interval_hours * 3600
    while True:
        try:
            await asyncio.to_thread(sync_configured_clinic)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Holiday sync scheduler cycle failed")
        await asyncio.sleep(interval)
