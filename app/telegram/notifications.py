from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.error import TelegramError

from app.database.supabase import get_supabase_client


logger = logging.getLogger(__name__)

POLL_SECONDS = 60
SERVED_INTERVAL_SECONDS = 300
SERVED_GRACE_MINUTES = 30

SUPPORTED_TYPES = frozenset({
    "booking_confirmation",
    "reschedule_confirmation",
    "cancellation_confirmation",
    "appointment_reminder",
    "final_reminder",
    "feedback_request",
})

REMINDER_TYPES = frozenset({"appointment_reminder", "final_reminder"})


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


def _cancel_notification(notification_id: str, reason: str) -> None:
    client = get_supabase_client()
    client.table("outbound_notifications").update(
        {"status": "cancelled", "last_error": reason[:1000]}
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


def _load_appointment(appointment_id: str | None) -> dict | None:
    if not appointment_id:
        return None
    client = get_supabase_client()
    rows = (
        client.table("appointments")
        .select(
            "id,appointment_reference,status,start_at,end_at,clinic_id,contact_id,"
            "booking_request_id,payment_mode,payment_status,dentists(name),"
            "appointment_services(service_name),patients(full_name),clinics(name,timezone)"
        )
        .eq("id", appointment_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


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


def _format_when(value: str | None, timezone_name: str) -> str:
    if not value:
        return "time pending"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        parsed = parsed.astimezone(ZoneInfo(timezone_name))
        return parsed.strftime("%a %d %b %Y, %I:%M %p")
    except Exception:
        return str(value)


def _format_notification(
    notification: dict,
    appointment: dict | None,
) -> str | None:
    notification_type = notification.get("notification_type")
    template = notification.get("template_data") or {}

    if appointment:
        clinic = appointment.get("clinics") or {}
        timezone_name = clinic.get("timezone") or template.get("timezone") or "UTC"
        reference = (
            appointment.get("appointment_reference")
            or template.get("appointment_reference")
            or "—"
        )
        when = _format_when(appointment.get("start_at"), timezone_name)
        doctor = (
            (appointment.get("dentists") or {}).get("name")
            or template.get("dentist_name")
            or "—"
        )
        services = ", ".join(
            line.get("service_name")
            for line in appointment.get("appointment_services") or []
            if line.get("service_name")
        ) or template.get("services") or "—"
        patient_name = (appointment.get("patients") or {}).get("full_name")
        clinic_name = clinic.get("name") or template.get("clinic_name") or "Marina Smile"
        payment = (
            f"{appointment.get('payment_mode') or 'cash'} · "
            f"{appointment.get('payment_status') or 'unpaid'}"
        )
    else:
        reference = template.get("appointment_reference") or "—"
        when = _format_when(template.get("start_at"), template.get("timezone") or "UTC")
        doctor = template.get("dentist_name") or "—"
        services = template.get("services") or "—"
        patient_name = template.get("patient_name")
        clinic_name = template.get("clinic_name") or "Marina Smile"
        payment = (
            f"{template.get('payment_mode') or 'cash'} · "
            f"{template.get('payment_status') or 'unpaid'}"
        )

    greeting = f"Hi {patient_name},\n\n" if patient_name else ""

    if notification_type == "appointment_reminder":
        return (
            f"⏰ Reminder: your appointment is in 24 hours.\n\n"
            f"{greeting}Reference: {reference}\n"
            f"When: {when}\n"
            f"Doctor: {doctor}\n"
            f"Services: {services}\n"
            f"Payment: {payment}\n\n"
            "Use My Appointments in the bot to reschedule or cancel."
        )
    if notification_type == "final_reminder":
        return (
            f"⏰ Final reminder: your appointment is in 2 hours.\n\n"
            f"{greeting}Reference: {reference}\n"
            f"When: {when}\n"
            f"Doctor: {doctor}\n"
            f"Services: {services}\n"
            f"Payment: {payment}\n\n"
            "Please arrive a few minutes early. Use My Appointments to reschedule or cancel."
        )
    if notification_type == "feedback_request":
        return (
            f"Thank you for visiting {clinic_name}.\n\n"
            f"{greeting}We'd love your feedback about your appointment {reference} "
            f"with {doctor} on {when}.\n\n"
            "Reply to this message with a few words about your experience."
        )
    if notification_type == "booking_confirmation":
        return (
            f"✅ Booking confirmed.\n\n"
            f"{greeting}Reference: {reference}\n"
            f"When: {when}\n"
            f"Doctor: {doctor}\n"
            f"Services: {services}\n"
            f"Payment: {payment}"
        )
    if notification_type == "reschedule_confirmation":
        return (
            f"✅ Appointment rescheduled.\n\n"
            f"{greeting}Reference: {reference}\n"
            f"New time: {when}\n"
            f"Doctor: {doctor}\n"
            f"Services: {services}\n"
            f"Payment: {payment}"
        )
    if notification_type == "cancellation_confirmation":
        return (
            f"✅ Appointment cancelled.\n\n"
            f"{greeting}Reference: {reference}\n"
            f"Original time: {when}\n"
            f"Doctor: {doctor}\n"
            f"Services: {services}\n\n"
            "Use Book Appointment to choose a new time."
        )
    return None


async def _deliver(bot: Bot, notification: dict) -> None:
    notification_id = notification["id"]
    notification_type = notification.get("notification_type")
    try:
        if notification_type not in SUPPORTED_TYPES:
            await asyncio.to_thread(
                _complete_notification,
                notification_id,
                error=f"Unsupported notification type: {notification_type}",
            )
            return

        appointment = await asyncio.to_thread(
            _load_appointment, notification.get("appointment_id")
        )

        if notification_type in REMINDER_TYPES:
            if appointment and appointment.get("status") in {
                "cancelled", "completed", "no_show",
            }:
                await asyncio.to_thread(
                    _cancel_notification,
                    notification_id,
                    "Appointment is no longer active",
                )
                return

        if notification_type == "feedback_request":
            if not appointment or appointment.get("status") != "completed":
                await asyncio.to_thread(
                    _cancel_notification,
                    notification_id,
                    "Appointment is not completed",
                )
                return

        text = _format_notification(notification, appointment)
        if not text:
            await asyncio.to_thread(
                _complete_notification,
                notification_id,
                error="Notification type could not be formatted",
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

        await bot.send_message(chat_id=int(chat_id), text=text)
        await asyncio.to_thread(_complete_notification, notification_id)
    except TelegramError as exc:
        logger.warning("Telegram notification delivery failed: %s", exc)
        await asyncio.to_thread(_complete_notification, notification_id, error=str(exc))
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        logger.exception("Notification delivery failed")
        await asyncio.to_thread(_complete_notification, notification_id, error=str(exc))


async def run_notification_worker(bot: Bot) -> None:
    """Deliver due notifications and reconcile served appointments forever."""
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
