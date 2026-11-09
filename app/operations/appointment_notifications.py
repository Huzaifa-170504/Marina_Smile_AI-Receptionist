from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client

from app.database.supabase import get_supabase_client


logger = logging.getLogger(__name__)

REMINDER_TYPES = ("appointment_reminder", "final_reminder")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert(client: Client, payload: dict) -> None:
    try:
        client.table("outbound_notifications").insert(payload).execute()
    except APIError as exc:
        if exc.code != "23505":
            raise


def record_immediate_notification(
    client: Client | None,
    *,
    clinic_id: str,
    contact_id: str | None,
    notification_type: str,
    booking_request_id: str | None = None,
    appointment_id: str | None = None,
    template_data: dict | None = None,
    event_key: str | None = None,
) -> None:
    """Record an immediately delivered Telegram confirmation.

    The bot sends the user-facing message synchronously; this row is the
    durable delivery record and prevents duplicate sends by the worker.
    """
    if not contact_id:
        return
    supabase = client or get_supabase_client()
    now = _now().isoformat()
    _insert(
        supabase,
        {
            "clinic_id": clinic_id,
            "contact_id": contact_id,
            "booking_request_id": booking_request_id,
            "appointment_id": appointment_id,
            "notification_type": notification_type,
            "channel": "telegram",
            "scheduled_at": now,
            "sent_at": now,
            "status": "sent",
            "event_key": event_key or f"{notification_type}:{appointment_id or booking_request_id}:{uuid4().hex[:10]}",
            "template_data": template_data or {},
        },
    )


def schedule_appointment_reminders(
    client: Client | None,
    *,
    clinic_id: str,
    contact_id: str | None,
    booking_request_id: str | None,
    appointment_id: str,
    start_at: datetime,
    template_data: dict | None = None,
) -> None:
    """Schedule or move the 24-hour and 2-hour reminders for an appointment."""
    if not contact_id:
        return
    supabase = client or get_supabase_client()
    now = _now()
    schedule = (
        ("appointment_reminder", timedelta(hours=24)),
        ("final_reminder", timedelta(hours=2)),
    )
    for notification_type, lead in schedule:
        scheduled_at = start_at - lead
        if scheduled_at <= now:
            scheduled_at = now + timedelta(minutes=1)

        existing = (
            supabase.table("outbound_notifications")
            .select("id,status,reminder_number")
            .eq("clinic_id", clinic_id)
            .eq("appointment_id", appointment_id)
            .eq("notification_type", notification_type)
            .execute()
            .data
            or []
        )
        pending = [row for row in existing if row.get("status") == "pending"]
        if pending:
            supabase.table("outbound_notifications").update(
                {
                    "scheduled_at": scheduled_at.isoformat(),
                    "template_data": template_data or {},
                }
            ).eq("clinic_id", clinic_id).eq("id", pending[0]["id"]).eq(
                "status", "pending"
            ).execute()
            continue

        next_number = max(
            [int(row.get("reminder_number") or 0) for row in existing] or [0]
        ) + 1
        _insert(
            supabase,
            {
                "clinic_id": clinic_id,
                "contact_id": contact_id,
                "booking_request_id": booking_request_id,
                "appointment_id": appointment_id,
                "notification_type": notification_type,
                "channel": "telegram",
                "reminder_number": next_number,
                "scheduled_at": scheduled_at.isoformat(),
                "status": "pending",
                "event_key": f"{notification_type}:{appointment_id}:{uuid4().hex[:10]}",
                "template_data": template_data or {},
            },
        )


def cancel_pending_notifications(
    client: Client | None,
    *,
    clinic_id: str,
    appointment_id: str,
) -> None:
    """Cancel reminders and feedback requests that have not been delivered."""
    supabase = client or get_supabase_client()
    try:
        supabase.table("outbound_notifications").update({"status": "cancelled"}).eq(
            "clinic_id", clinic_id
        ).eq("appointment_id", appointment_id).in_(
            "status", ["pending", "processing"]
        ).execute()
    except Exception:
        logger.exception(
            "Failed to cancel pending appointment notifications",
            extra={"appointment_id": appointment_id},
        )
