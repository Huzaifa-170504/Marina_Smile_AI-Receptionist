from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from telegram import Bot
from telegram.error import TelegramError

from app.database.supabase import get_supabase_client


logger = logging.getLogger(__name__)

POLL_SECONDS = 60
SERVED_INTERVAL_SECONDS = 300
SERVED_GRACE_MINUTES = 30


def _single_row(data: Any) -> dict | None:
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


def _claim_notification() -> dict | None:
    client = get_supabase_client()
    result = client.rpc("claim_outbound_notification", {}).execute()
    return _single_row(result.data)


def _complete_notification(notification_id: str, *, error: str | None = None) -> None:
    client = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    if error is None:
        client.table("outbound_notifications").update(
            {"status": "sent", "sent_at": now, "last_error": None}
        ).eq("id", notification_id).execute()
    else:
        client.table("outbound_notifications").update(
            {"status": "failed", "last_error": error[:1000]}
        ).eq("id", notification_id).execute()


def _load_contact(contact_id: str) -> dict | None:
    client = get_supabase_client()
    rows = (
        client.table("contacts")
        .select("id,telegram_chat_id,name,phone_number,email")
        .eq("id", contact_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def _format_reminder(template: dict[str, Any]) -> str:
    start_text = template.get("start_at") or ""
    try:
        start = datetime.fromisoformat(str(start_text).replace("Z", "+00:00"))
        start_text = start.strftime("%a %d %b %Y, %I:%M %p")
    except ValueError:
        pass
    payment_mode = template.get("payment_mode") or "cash"
    payment_status = template.get("payment_status") or "unpaid"
    return (
        "Appointment reminder — 24 hours to go\n\n"
        f"Reference: {template.get('appointment_reference') or '—'}\n"
        f"Time: {start_text}\n"
        f"Doctor: {template.get('dentist_name') or '—'}\n"
        f"Services: {template.get('services') or '—'}\n"
        f"Payment: {payment_mode} · {payment_status}\n\n"
        "Reply in the bot with @ Booking status to review or cancel."
    )


def _serve_due_appointments() -> int:
    client = get_supabase_client()
    result = client.rpc(
        "mark_due_appointments_served",
        {"p_grace_minutes": SERVED_GRACE_MINUTES},
    ).execute()
    data = result.data
    if isinstance(data, list):
        return int(data[0] or 0) if data else 0
    return int(data or 0)


async def _deliver(bot: Bot, notification: dict) -> None:
    notification_id = notification["id"]
    try:
        if notification.get("notification_type") != "appointment_reminder":
            await asyncio.to_thread(
                _complete_notification,
                notification_id,
                error=f"Unsupported notification type: {notification.get('notification_type')}",
            )
            return

        contact = await asyncio.to_thread(_load_contact, notification["contact_id"])
        chat_id = contact.get("telegram_chat_id") if contact else None
        if not chat_id:
            await asyncio.to_thread(
                _complete_notification,
                notification_id,
                error="Contact has no Telegram chat ID",
            )
            return

        template = notification.get("template_data") or {}
        await bot.send_message(chat_id=int(chat_id), text=_format_reminder(template))
        await asyncio.to_thread(_complete_notification, notification_id)
    except TelegramError as exc:
        logger.warning("Telegram reminder delivery failed: %s", exc)
        await asyncio.to_thread(_complete_notification, notification_id, error=str(exc))
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        logger.exception("Reminder delivery failed")
        await asyncio.to_thread(_complete_notification, notification_id, error=str(exc))


async def run_notification_worker(bot: Bot) -> None:
    """Deliver due reminders and reconcile served appointments forever."""
    served_seconds = 0
    while True:
        try:
            if served_seconds >= SERVED_INTERVAL_SECONDS:
                served = await asyncio.to_thread(_serve_due_appointments)
                if served:
                    logger.info("Marked %s due appointments as served/no-show", served)
                served_seconds = 0

            while True:
                notification = await asyncio.to_thread(_claim_notification)
                if notification is None:
                    break
                await _deliver(bot, notification)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification worker cycle failed")

        await asyncio.sleep(POLL_SECONDS)
        served_seconds += POLL_SECONDS
