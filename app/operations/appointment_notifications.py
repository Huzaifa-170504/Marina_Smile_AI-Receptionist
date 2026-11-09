from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client

from app.database.supabase import get_supabase_client


logger = logging.getLogger(__name__)

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
