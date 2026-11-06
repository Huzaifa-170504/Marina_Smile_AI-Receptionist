from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.ai.knowledge_answer import answer_knowledge_question
from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.appointment_change_models import AppointmentActor, AppointmentActorType
from app.operations.cancellation import cancel_appointment
from app.operations.patient_booking import (
    PatientBookingError,
    active_booking_for_patient,
    create_booking_request,
    describe_active_booking,
    finalize_booking_request,
    normalize_phone,
    resolve_or_register_patient,
)
from app.operations.slot_finder import find_available_slots
from app.repositories.clinics import get_active_clinic
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.repositories.services import list_active_services
from app.telegram.notifications import run_notification_worker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logger = logging.getLogger(__name__)
STATE = "marina_state"
BUCKET = "payment-proofs"
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
MENU = (
    ("@ Book appointment", "@ See services"),
    ("@ See doctors", "@ Booking status"),
    ("@ Cancel booking", "@ Uploaded payments"),
    ("@ Ask MarinaSmileAI", "@ Main menu"),
)


def _menu():
    return ReplyKeyboardMarkup([[KeyboardButton(item) for item in row] for row in MENU],
                               resize_keyboard=True, is_persistent=True)


def _client():
    return get_supabase_client()


def _clinic_id():
    return str(get_settings().default_clinic_id)


def _phone_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalize_identifier(value: str) -> tuple[str, str] | None:
    value = unicodedata.normalize("NFKC", value).strip()
    if "@" in value:
        email = value.casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            return None
        return "email", email
    phone = _phone_digits(value)
    if not 7 <= len(phone) <= 15:
        return None
    return "phone", phone


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(amount, currency="AED") -> str:
    if amount is None:
        return f"{currency} amount pending"
    return f"{currency} {float(amount):,.2f}"


def _service_names(rows) -> str:
    names = []
    for row in rows or []:
        if isinstance(row, dict):
            name = row.get("service_name") or (row.get("services") or {}).get("name")
        else:
            name = str(row)
        if name:
            names.append(name)
    return ", ".join(names) if names else "Appointment"


def _when_text(value: str | None, timezone_name: str) -> str:
    parsed = _parse_ts(value)
    if parsed is None:
        return value or "time pending"
    try:
        parsed = parsed.astimezone(ZoneInfo(timezone_name))
    except Exception:
        pass
    return parsed.strftime("%a %d %b %Y, %I:%M %p")


async def _private(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type == "private":
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Please use this bot in a private chat for patient privacy.")
    return False


async def _require_patient(update, context):
    patient = context.user_data.get("patient")
    if patient:
        return patient
    await update.effective_message.reply_text(
        "Enter the phone number or email registered with the clinic. You can also share your phone, then enter your full name.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Share registered phone", request_contact=True)]],
                                         resize_keyboard=True, one_time_keyboard=True))
    context.user_data[STATE] = "identifier"
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _private(update):
        context.user_data.pop(STATE, None)
        await update.effective_message.reply_text("Welcome to Marina Smile. Choose an option:",
                                                   reply_markup=_menu())


async def _contact(update, context):
    if not await _private(update):
        return
    if context.user_data.get(STATE) != "identifier":
        return
    contact = update.effective_message.contact
    if not contact or not update.effective_user or contact.user_id != update.effective_user.id or not contact.phone_number:
        await update.effective_message.reply_text("Please share your own phone using the button.")
        return
    context.user_data["patient_identifier"] = ("phone", _phone_digits(contact.phone_number))
    context.user_data[STATE] = "name"
    await update.effective_message.reply_text("Enter your full name exactly as registered with the clinic.")


async def _verify_identifier(update, context):
    identifier = _normalize_identifier(update.effective_message.text or "")
    if identifier is None:
        await update.effective_message.reply_text("Enter a valid registered phone number or email address.")
        return
    context.user_data["patient_identifier"] = identifier
    context.user_data[STATE] = "name"
    await update.effective_message.reply_text("Enter your full name exactly as registered with the clinic.")


async def _verify_name(update, context):
    try:
        identifier_type, identifier = context.user_data.pop("patient_identifier", ("", ""))
        chat_id = update.effective_chat.id if update.effective_chat else None
        patient = await asyncio.to_thread(
            resolve_or_register_patient,
            _clinic_id(),
            identifier_type,
            identifier,
            update.effective_message.text.strip(),
            telegram_chat_id=chat_id,
        )
    except PatientBookingError as exc:
        context.user_data.pop(STATE, None)
        await update.effective_message.reply_text(str(exc), reply_markup=_menu())
        return
    except Exception:
        logger.exception("Telegram patient lookup failed")
        await update.effective_message.reply_text("I couldn't verify your record right now. Please try later.")
        return
    context.user_data.pop(STATE, None)
    context.user_data["patient"] = patient
    await update.effective_message.reply_text(f"Patient record linked for {patient['name']}.", reply_markup=_menu())


async def _services(update):
    try:
        rows = await asyncio.to_thread(lambda: _client().table("services").select(
            "name,price_from,currency,active").eq("clinic_id", _clinic_id()).eq("active", True).execute().data or [])
        lines = ["Marina Smile services:"]
        for row in rows:
            price = row.get("price_from")
            lines.append(f"• {row['name']}" + (f" · from {row.get('currency') or 'AED'} {price}" if price is not None else ""))
        await update.effective_message.reply_text("\n".join(lines) if rows else "No active services are listed.",
                                                   reply_markup=_menu())
    except Exception:
        logger.exception("Telegram service listing failed")
        await update.effective_message.reply_text("Services are temporarily unavailable.")


async def _doctors(update):
    def load():
        client, clinic = _client(), _clinic_id()
        dentists = client.table("dentists").select(
            "id,name,specialty,dentist_services(services(name))"
        ).eq("clinic_id", clinic).eq("active", True).order("name").execute().data or []
        return dentists

    try:
        dentists = await asyncio.to_thread(load)
        if not dentists:
            await update.effective_message.reply_text("No active doctors are listed.", reply_markup=_menu())
            return
        lines = ["Marina Smile doctors:"]
        for dentist in dentists:
            services = [
                (link.get("services") or {}).get("name")
                for link in dentist.get("dentist_services") or []
            ]
            services = [name for name in services if name]
            detail = f" · {dentist['specialty']}" if dentist.get("specialty") else ""
            services_text = f"\n   Services: {', '.join(services)}" if services else ""
            lines.append(f"• {dentist['name']}{detail}{services_text}")
        await update.effective_message.reply_text("\n".join(lines), reply_markup=_menu())
    except Exception:
        logger.exception("Telegram doctor listing failed")
        await update.effective_message.reply_text("Doctors are temporarily unavailable.")


def _load_service_rows(clinic_id: str) -> dict:
    rows = _client().table("services").select(
        "id,name,duration_minutes,price_from,currency,payment_required,booking_deposit_amount"
    ).eq("clinic_id", clinic_id).eq("active", True).order("name").execute().data or []
    return {row["id"]: row for row in rows}


def _service_keyboard(booking: dict) -> InlineKeyboardMarkup:
    selected = set(booking.get("selected") or [])
    rows = []
    for service_id, row in booking.get("services", {}).items():
        mark = "✅" if service_id in selected else "⬜"
        price = row.get("price_from")
        suffix = f" · {row.get('currency') or 'AED'} {price}" if price is not None else ""
        rows.append([InlineKeyboardButton(f"{mark} {row['name']}{suffix}", callback_data=f"toggle:{service_id}")])
    rows.append([InlineKeyboardButton("Continue with selected services", callback_data="services-done")])
    return InlineKeyboardMarkup(rows)


def _selected_summary(booking: dict) -> tuple[list[dict], float, int]:
    selected = booking.get("selected") or []
    services = booking.get("services", {})
    rows = []
    total = 0.0
    duration = 0
    for service_id in selected:
        row = services.get(service_id)
        if not row:
            continue
        price = float(row.get("price_from") or 0)
        rows.append({
            "service_id": service_id,
            "name": row["name"],
            "price_amount": price,
            "duration_minutes": row["duration_minutes"],
            "currency": row.get("currency") or "AED",
        })
        total += price
        duration += int(row["duration_minutes"])
    return rows, total, duration


async def _begin_booking(update, context):
    patient = await _require_patient(update, context)
    if not patient:
        return
    try:
        existing = await asyncio.to_thread(
            active_booking_for_patient, patient["clinic_id"], patient["patient_id"]
        )
        if existing:
            await update.effective_message.reply_text(describe_active_booking(existing), reply_markup=_menu())
            return
        services = await asyncio.to_thread(_load_service_rows, patient["clinic_id"])
    except Exception:
        logger.exception("Telegram booking services lookup failed")
        await update.effective_message.reply_text("Booking is temporarily unavailable.")
        return
    if not services:
        await update.effective_message.reply_text("No active services are available to book.")
        return
    context.user_data["booking"] = {"services": services, "selected": []}
    context.user_data[STATE] = "booking"
    await update.effective_message.reply_text(
        "Choose one or more services. Your bill is calculated automatically:",
        reply_markup=_service_keyboard(context.user_data["booking"]),
    )


def _eligible_dentists(patient: dict, service_ids: list[str]):
    dentists = list_active_dentists(patient["clinic_id"])
    eligible = dentists
    for service_id in service_ids:
        allowed = {
            dentist.id
            for dentist in filter_dentists_for_service(patient["clinic_id"], dentists, service_id)
        }
        eligible = [dentist for dentist in eligible if dentist.id in allowed]
    return eligible


def _doctor_keyboard(booking: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(name, callback_data=f"doctor:{dentist_id}")]
        for dentist_id, name in booking.get("dentists", {}).items()
    ]
    rows.append([InlineKeyboardButton("Change services", callback_data="back-services")])
    return InlineKeyboardMarkup(rows)


def _payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cash in hand", callback_data="mode:cash")],
        [InlineKeyboardButton("Online payment", callback_data="mode:online")],
        [InlineKeyboardButton("Change time", callback_data="change-date")],
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm booking", callback_data="confirm-booking")],
        [InlineKeyboardButton("Change payment method", callback_data="change-mode")],
    ])


async def _review_text(context, patient: dict) -> tuple[str, dict]:
    booking = context.user_data["booking"]
    services, total, duration = _selected_summary(booking)
    slot = booking["slot"]
    clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
    when = _when_text(slot["start_at"], clinic.timezone)
    mode = booking.get("payment_mode")
    mode_text = "Cash in hand · unpaid" if mode == "cash" else "Online payment · awaiting receipt"
    doctor = booking.get("dentists", {}).get(booking.get("dentist_id"), "—")
    text = (
        "Review your booking before confirming:\n\n"
        f"Services: {', '.join(row['name'] for row in services)}\n"
        f"Duration: {duration} minutes\n"
        f"Doctor: {doctor}\n"
        f"When: {when}\n"
        f"Bill: {_money(total, booking.get('currency') or 'AED')}\n"
        f"Payment: {mode_text}\n\n"
        "Selected services cannot be edited after you confirm."
    )
    return text, {"services": services, "total": total, "duration": duration}


async def _callbacks(update, context):
    if not await _private(update):
        return
    query = update.callback_query
    await query.answer()
    patient = context.user_data.get("patient")
    if not patient:
        await query.edit_message_text("Please verify your patient record first with @ Book appointment.")
        return
    client = _client()
    data = query.data or ""

    if data.startswith("toggle:"):
        booking = context.user_data.get("booking")
        if not booking:
            await query.edit_message_text("Start booking again with @ Book appointment.")
            return
        service_id = data.removeprefix("toggle:")
        selected = set(booking.get("selected") or [])
        if service_id in selected:
            selected.discard(service_id)
        elif service_id in booking.get("services", {}):
            selected.add(service_id)
        booking["selected"] = [sid for sid in booking["services"] if sid in selected]
        rows, total, duration = _selected_summary(booking)
        await query.edit_message_text(
            f"Selected {len(rows)} service(s) · {duration} min · bill {_money(total, rows[0]['currency'] if rows else 'AED')}.\n"
            "Tap services to change them, then continue.",
            reply_markup=_service_keyboard(booking),
        )
        return

    if data == "back-services":
        booking = context.user_data.get("booking")
        if booking:
            await query.edit_message_text(
                "Choose one or more services. Your bill is calculated automatically:",
                reply_markup=_service_keyboard(booking),
            )
        return

    if data == "services-done":
        booking = context.user_data.get("booking")
        if not booking or not booking.get("selected"):
            await query.answer("Select at least one service.", show_alert=True)
            return
        services, total, _duration = _selected_summary(booking)
        try:
            dentists = await asyncio.to_thread(_eligible_dentists, patient, list(booking["selected"]))
        except Exception:
            logger.exception("Telegram dentist eligibility lookup failed")
            await query.edit_message_text("Doctors are temporarily unavailable. Please try again.")
            return
        if not dentists:
            await query.edit_message_text("No single active doctor provides all selected services. Change the services.")
            return
        booking["dentists"] = {dentist.id: dentist.name for dentist in dentists}
        booking["currency"] = services[0]["currency"]
        await query.edit_message_text(
            f"Bill so far: {_money(total, booking['currency'])}.\nChoose an available doctor:",
            reply_markup=_doctor_keyboard(booking),
        )
        return

    if data.startswith("doctor:"):
        booking = context.user_data.get("booking") or {}
        doctor_id = data.removeprefix("doctor:")
        if doctor_id not in booking.get("dentists", {}):
            await query.edit_message_text("That doctor selection expired. Please start booking again.")
            return
        booking["dentist_id"] = doctor_id
        context.user_data[STATE] = "date"
        await query.edit_message_text(
            f"Selected {booking['dentists'][doctor_id]}. Send a date as YYYY-MM-DD."
        )
        return

    if data.startswith("slot:"):
        booking = context.user_data.get("booking") or {}
        try:
            slot = context.user_data["slots"][int(data.removeprefix("slot:"))]
        except (KeyError, ValueError, IndexError):
            await query.edit_message_text("That time choice expired. Please check availability again.")
            return
        booking["slot"] = {"start_at": slot.start_at.isoformat(), "end_at": slot.end_at.isoformat()}
        context.user_data.pop("slots", None)
        await query.edit_message_text(
            "How would you like to pay?\n"
            "• Cash in hand: the booking is confirmed immediately as unpaid and you pay at the clinic.\n"
            "• Online payment: upload a receipt, and the clinic verifies it before confirming.",
            reply_markup=_payment_keyboard(),
        )
        return

    if data == "change-date":
        context.user_data[STATE] = "date"
        await query.edit_message_text("Send the appointment date as YYYY-MM-DD.")
        return

    if data.startswith("mode:"):
        booking = context.user_data.get("booking") or {}
        mode = data.removeprefix("mode:")
        if mode not in {"cash", "online"}:
            await query.edit_message_text("Choose cash in hand or online payment.")
            return
        booking["payment_mode"] = mode
        text, summary = await _review_text(context, patient)
        booking["review_summary"] = summary
        await query.edit_message_text(text, reply_markup=_confirm_keyboard())
        return

    if data == "change-mode":
        await query.edit_message_text(
            "How would you like to pay?",
            reply_markup=_payment_keyboard(),
        )
        return

    if data == "confirm-booking":
        booking = context.user_data.get("booking") or {}
        if not booking.get("selected") or not booking.get("dentist_id") or not booking.get("slot"):
            await query.edit_message_text("Your booking details expired. Please start again with @ Book appointment.")
            return
        try:
            existing = await asyncio.to_thread(
                active_booking_for_patient, patient["clinic_id"], patient["patient_id"]
            )
        except Exception:
            logger.exception("Telegram duplicate booking check failed")
            await query.edit_message_text("I couldn't verify your bookings. Please try again.")
            return
        if existing:
            await query.message.reply_text(describe_active_booking(existing), reply_markup=_menu())
            return

        services, total, _duration = _selected_summary(booking)
        start_at = _parse_ts(booking["slot"]["start_at"])
        end_at = _parse_ts(booking["slot"]["end_at"])
        if start_at is None or end_at is None:
            await query.edit_message_text("The selected time expired. Please check availability again.")
            return
        try:
            request = await asyncio.to_thread(
                create_booking_request,
                clinic_id=patient["clinic_id"],
                patient=patient,
                services=services,
                dentist_id=booking["dentist_id"],
                start_at=start_at,
                end_at=end_at,
                payment_mode=booking.get("payment_mode") or "cash",
            )
        except PatientBookingError as exc:
            await query.message.reply_text(str(exc), reply_markup=_menu())
            return
        except Exception:
            logger.exception("Telegram booking request insert failed")
            await query.edit_message_text("I couldn't save the booking request. Please try again.")
            return

        context.user_data.pop(STATE, None)
        if booking.get("payment_mode") == "cash":
            try:
                result = await asyncio.to_thread(
                    finalize_booking_request, patient["clinic_id"], request["id"]
                )
            except Exception:
                logger.exception("Telegram cash booking confirmation failed", extra={"request_id": request["id"]})
                await query.edit_message_text(
                    "Your booking is saved, but confirmation is delayed. Check @ Booking status shortly.",
                    reply_markup=_menu(),
                )
                return
            text = await _confirmation_text(patient, result, request)
            await query.message.reply_text(text, reply_markup=_menu())
            context.user_data.pop("booking", None)
            return

        context.user_data["request_id"] = request["id"]
        context.user_data["booking"] = booking
        context.user_data[STATE] = "payment"
        await query.edit_message_text(
            f"Booking request saved for {_when_text(booking['slot']['start_at'], 'UTC')}.\n"
            f"Upload your receipt for {_money(total, booking.get('currency') or 'AED')}. "
            "The clinic verifies online payments before confirming the appointment.",
        )
        return

    if data.startswith("cancel-request:"):
        request_id = data.removeprefix("cancel-request:")
        expected = context.user_data.get("cancel_status", {}).get(request_id)
        if not expected:
            await query.edit_message_text("That booking request is no longer available to cancel.")
            return
        try:
            await asyncio.to_thread(lambda: client.rpc("transition_booking_request", {
                "p_booking_request_id": request_id, "p_expected_status": expected,
                "p_target_status": "cancelled"}).execute())
            await query.edit_message_text("Booking request cancelled.")
        except Exception:
            logger.exception("Telegram booking request cancellation failed")
            await query.edit_message_text("The request could not be cancelled. Check its current status.")
        return

    if data.startswith("cancel-appointment:"):
        try:
            result = await asyncio.to_thread(cancel_appointment, patient["clinic_id"], patient["contact_id"],
                data.removeprefix("cancel-appointment:"),
                actor=AppointmentActor(actor_type=AppointmentActorType.PATIENT, actor_id=patient["patient_id"]))
            ok = result.status.value in {"CANCELLED", "ALREADY_CANCELLED"}
            if ok:
                await asyncio.to_thread(
                    lambda: client.table("outbound_notifications").update({"status": "cancelled"})
                    .eq("clinic_id", patient["clinic_id"])
                    .eq("appointment_id", data.removeprefix("cancel-appointment:"))
                    .eq("status", "pending").execute()
                )
            await query.edit_message_text("Appointment cancelled." if ok else (result.reason or "The appointment could not be cancelled."))
        except Exception:
            logger.exception("Telegram appointment cancellation failed")
            await query.edit_message_text("The appointment could not be cancelled right now.")
        return


async def _confirmation_text(patient: dict, result: dict, request: dict) -> str:
    if result.get("status") != "confirmed":
        return result.get("reason") or "Your booking is saved, but confirmation is delayed. Check @ Booking status shortly."
    appointment = result["appointment"]
    services = result.get("services") or []
    clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
    when = _when_text(appointment.get("start_at") or request.get("preferred_window_start"), clinic.timezone)
    mode = appointment.get("payment_mode") or request.get("payment_mode") or "cash"
    payment_status = appointment.get("payment_status") or "unpaid"
    return (
        "Appointment confirmed\n\n"
        f"Reference: {appointment.get('appointment_reference') or '—'}\n"
        f"When: {when}\n"
        f"Doctor: {result.get('dentist_name') or '—'}\n"
        f"Services: {_service_names(services)}\n"
        f"Bill: {_money(appointment.get('total_amount') if appointment.get('total_amount') is not None else request.get('total_amount'), appointment.get('currency') or request.get('currency') or 'AED')}\n"
        f"Payment: {mode} · {payment_status}\n"
        f"Status: {appointment.get('status') or 'confirmed'}"
    )


async def _find_slots(update, context):
    msg = update.effective_message
    try:
        requested_date = date.fromisoformat(msg.text.strip())
        patient, booking = context.user_data["patient"], context.user_data["booking"]
        services, _total, duration = _selected_summary(booking)
        if not services:
            context.user_data.pop(STATE, None)
            await msg.reply_text("Your service selection expired. Please start booking again.", reply_markup=_menu())
            return
        clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
        tz = ZoneInfo(clinic.timezone)
        start = datetime.combine(requested_date, time.min, tzinfo=tz)
        result = await asyncio.to_thread(
            find_available_slots,
            patient["clinic_id"],
            booking["dentist_id"],
            None,
            start,
            start + timedelta(days=1),
            20,
            duration_override_minutes=duration,
        )
    except ValueError:
        await msg.reply_text("Send the date as YYYY-MM-DD.")
        return
    except Exception:
        logger.exception("Telegram availability search failed")
        await msg.reply_text("I couldn't check availability right now. Please try later.")
        return
    if not result.slots:
        await msg.reply_text("No available times for that doctor on this date. Send another date.")
        return
    context.user_data["slots"] = result.slots
    rows = [
        [InlineKeyboardButton(
            slot.start_at.astimezone(tz).strftime("%a %d %b, %I:%M %p"),
            callback_data=f"slot:{i}",
        )]
        for i, slot in enumerate(result.slots)
    ]
    await msg.reply_text("Choose an available time:", reply_markup=InlineKeyboardMarkup(rows))


async def _submit_receipt(update, context):
    msg = update.effective_message
    patient, request_id = context.user_data.get("patient"), context.user_data.get("request_id")
    if not patient or not request_id:
        await msg.reply_text("Start a booking before uploading a payment receipt.")
        return
    if msg.photo:
        media, mime, ext = msg.photo[-1], "image/jpeg", "jpg"
    elif msg.document:
        media = msg.document
        mime = media.mime_type or ""
        ext = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}.get(mime)
        if not ext:
            await msg.reply_text("Upload a JPG, PNG, or PDF receipt.")
            return
    else:
        await msg.reply_text("Upload your receipt as a photo or PDF.")
        return
    if getattr(media, "file_size", 0) > MAX_RECEIPT_BYTES:
        await msg.reply_text("The receipt must be 10 MB or smaller.")
        return
    try:
        tgfile = await media.get_file()
        payload = bytes(await tgfile.download_as_bytearray())
        if len(payload) > MAX_RECEIPT_BYTES:
            raise ValueError("Receipt is larger than 10 MB")
        await asyncio.to_thread(_store_receipt, patient, request_id, payload, mime, ext)
    except Exception:
        logger.exception("Telegram receipt submission failed")
        await msg.reply_text("I couldn't submit that receipt. Please try again.")
        return
    context.user_data.pop("request_id", None)
    context.user_data.pop("booking", None)
    context.user_data.pop(STATE, None)
    await msg.reply_text(
        "Receipt uploaded. Status: payment submitted, awaiting clinic verification. "
        "Use @ Booking status to check again.",
        reply_markup=_menu(),
    )


def _store_receipt(patient, request_id, payload, mime, extension):
    client, clinic = _client(), patient["clinic_id"]
    request = client.table("booking_requests").select(
        "id,status,service_id,total_amount,currency,payment_mode"
    ).eq("clinic_id", clinic).eq("id", request_id).eq(
        "patient_id", patient["patient_id"]).single().execute().data
    if request["status"] not in {"awaiting_payment", "payment_rejected"}:
        raise ValueError("Booking request is not accepting payment")
    path = f"{clinic}/{patient['patient_id']}/{request_id}/{uuid4()}.{extension}"
    storage = client.storage.from_(BUCKET)
    storage.upload(path, payload, {"content-type": mime, "upsert": "false"})
    payment_id = str(uuid4())
    try:
        client.table("payments").insert({
            "id": payment_id, "clinic_id": clinic, "booking_request_id": request_id,
            "patient_id": patient["patient_id"], "amount_expected": request.get("total_amount"),
            "currency": request.get("currency") or "AED", "method": "online",
            "proof_storage_path": path, "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(), "submission_idempotency_key": str(uuid4()),
        }).execute()
        client.rpc("transition_booking_request", {"p_booking_request_id": request_id,
            "p_expected_status": request["status"], "p_target_status": "payment_submitted"}).execute()
        client.table("booking_requests").update({"payment_status": "awaiting_verification"}).eq(
            "clinic_id", clinic).eq("id", request_id).execute()
    except Exception:
        logger.exception("Payment persistence failed")
        try:
            client.table("payments").delete().eq("clinic_id", clinic).eq("id", payment_id).execute()
            storage.remove([path])
        except Exception:
            logger.exception("Payment cleanup failed")
        raise


async def _status(update, patient):
    client = _client()
    try:
        clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
        requests = await asyncio.to_thread(lambda: client.table("booking_requests").select(
            "id,status,preferred_date,preferred_dentist_id,preferred_window_start,preferred_window_end,"
            "service_id,total_amount,currency,payment_mode,payment_status,created_at,"
            "booking_request_services(service_name),dentists(name)").eq(
            "clinic_id", patient["clinic_id"]).eq(
            "patient_id", patient["patient_id"]).order("created_at", desc=True).limit(10).execute().data or [])
        request_notes = []
        for request in requests:
            if request["status"] in {"payment_verified", "awaiting_slot_selection"}:
                try:
                    result = await asyncio.to_thread(
                        finalize_booking_request, patient["clinic_id"], request["id"]
                    )
                    if result.get("status") == "confirmed":
                        request["status"] = "converted_to_appointment"
                        request["payment_status"] = result.get("appointment", {}).get("payment_status", request.get("payment_status"))
                    elif result.get("reason"):
                        request_notes.append(f"Request {request['id'][:8]}: {result['reason']}")
                except Exception:
                    logger.exception("Telegram verified booking finalization failed", extra={"request_id": request["id"]})
                    request_notes.append(f"Request {request['id'][:8]} is approved, but confirmation is delayed.")

        appointments = await asyncio.to_thread(lambda: client.table("appointments").select(
            "id,appointment_reference,status,start_at,end_at,service_id,total_amount,currency,"
            "payment_mode,payment_status,dentists(name),appointment_services(service_name),"
            "appointment_visits(queue_status)").eq(
            "clinic_id", patient["clinic_id"]).eq(
            "patient_id", patient["patient_id"]).order("start_at", desc=True).limit(10).execute().data or [])

        if request_notes:
            await update.effective_message.reply_text("\n".join(request_notes))
        lines = []
        for request in requests:
            services = _service_names(request.get("booking_request_services") or [])
            doctor = (request.get("dentists") or {}).get("name") or "doctor pending"
            when = _when_text(request.get("preferred_window_start"), clinic.timezone)
            lines.append(
                f"• Request {request['id'][:8]} · {services} · {when} · "
                f"{request['status'].replace('_', ' ')} · {request.get('payment_mode') or 'payment pending'} "
                f"{request.get('payment_status') or 'unpaid'}"
            )
        for appointment in appointments:
            services = _service_names(appointment.get("appointment_services") or [])
            doctor = (appointment.get("dentists") or {}).get("name") or "doctor pending"
            when = _when_text(appointment.get("start_at"), clinic.timezone)
            queue = (appointment.get("appointment_visits") or {}).get("queue_status")
            lines.append(
                f"• {appointment.get('appointment_reference') or ('Appointment ' + appointment['id'][:8])} · "
                f"{services} · {when} · {doctor} · {appointment['status']} · "
                f"{appointment.get('payment_mode') or 'payment pending'} {appointment.get('payment_status') or 'unpaid'}"
                + (f" · queue {queue}" if queue else "")
            )
        await update.effective_message.reply_text("\n".join(lines) if lines else "No bookings found.", reply_markup=_menu())
    except Exception:
        logger.exception("Telegram booking status lookup failed")
        await update.effective_message.reply_text("Booking status is temporarily unavailable.")


async def _payments(update, patient):
    client = _client()
    try:
        rows = await asyncio.to_thread(lambda: client.table("payments").select(
            "booking_request_id,amount_expected,currency,status,method,proof_storage_path").eq(
            "clinic_id", patient["clinic_id"]).eq("patient_id", patient["patient_id"]).order(
            "created_at", desc=True).limit(10).execute().data or [])
        lines = ["Your uploaded payments:"]
        for row in rows:
            amount = f"{row.get('currency', 'AED')} {row['amount_expected']}" if row.get("amount_expected") is not None else "amount pending"
            text = f"• {amount} · {row.get('method') or 'online'} · {row['status']} · request {row['booking_request_id'][:8]}"
            path = row.get("proof_storage_path")
            if path:
                signed = await asyncio.to_thread(lambda p=path: client.storage.from_(BUCKET).create_signed_url(p, 300))
                url = signed.get("signedURL") or signed.get("signedUrl")
                if url:
                    text += f"\n  Receipt: {url}"
            lines.append(text)
        await update.effective_message.reply_text("\n".join(lines) if len(lines) > 1 else "No uploaded payments found.",
                                                   reply_markup=_menu(), disable_web_page_preview=True)
    except Exception:
        logger.exception("Telegram payment listing failed")
        await update.effective_message.reply_text("Uploaded payments are temporarily unavailable.")


async def _cancel_menu(update, patient, context):
    client = _client()
    try:
        requests = await asyncio.to_thread(lambda: client.table("booking_requests").select("id,status").eq(
            "clinic_id", patient["clinic_id"]).eq("patient_id", patient["patient_id"]).in_("status", [
            "awaiting_patient_info", "awaiting_payment", "payment_submitted", "payment_rejected",
            "payment_verified", "awaiting_slot_selection"]).limit(10).execute().data or [])
        appointments = await asyncio.to_thread(lambda: client.table("appointments").select("id,start_at").eq(
            "clinic_id", patient["clinic_id"]).eq("contact_id", patient["contact_id"]).in_(
            "status", ["pending", "confirmed"]).limit(10).execute().data or [])
        context.user_data["cancel_status"] = {r["id"]: r["status"] for r in requests}
        buttons = [[InlineKeyboardButton(f"Request {r['id'][:8]} · {r['status'].replace('_', ' ')}",
                                          callback_data=f"cancel-request:{r['id']}")] for r in requests]
        buttons += [[InlineKeyboardButton(f"Appointment {a['id'][:8]} · {a['start_at'][:16]}",
                                           callback_data=f"cancel-appointment:{a['id']}")] for a in appointments]
        await update.effective_message.reply_text("Choose a booking to cancel:" if buttons else "No active bookings to cancel.",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else _menu())
    except Exception:
        logger.exception("Telegram cancellation lookup failed")
        await update.effective_message.reply_text("Your bookings are temporarily unavailable.")


async def _ask_ai(update, question):
    try:
        result = await asyncio.to_thread(answer_knowledge_question, _clinic_id(), question)
        source = "Clinic information" if result.grounding in {"rag", "supabase"} else "Please confirm with reception"
        await update.effective_message.reply_text(f"{result.text}\n\n{source}", reply_markup=_menu())
    except Exception:
        logger.exception("MarinaSmileAI query failed")
        await update.effective_message.reply_text("I couldn't answer right now. Please contact reception.")


async def _text(update, context):
    if not await _private(update):
        return
    msg = update.effective_message
    if not msg or not msg.text:
        return
    state, text = context.user_data.get(STATE), msg.text.strip()
    if text in {item for row in MENU for item in row}:
        if text == "@ Main menu":
            context.user_data.pop(STATE, None)
            await msg.reply_text("Main menu", reply_markup=_menu())
        elif text == "@ See services":
            await _services(update)
        elif text == "@ See doctors":
            await _doctors(update)
        elif text == "@ Book appointment":
            await _begin_booking(update, context)
        elif text == "@ Booking status":
            patient = await _require_patient(update, context)
            if patient: await _status(update, patient)
        elif text == "@ Cancel booking":
            patient = await _require_patient(update, context)
            if patient: await _cancel_menu(update, patient, context)
        elif text == "@ Uploaded payments":
            patient = await _require_patient(update, context)
            if patient: await _payments(update, patient)
        elif text == "@ Ask MarinaSmileAI":
            context.user_data[STATE] = "ai"
            await msg.reply_text("Ask MarinaSmileAI a question about the clinic.")
        return
    if state == "identifier":
        await _verify_identifier(update, context)
    elif state == "name":
        await _verify_name(update, context)
    elif state == "date":
        await _find_slots(update, context)
    elif state == "payment":
        await msg.reply_text("Upload the payment receipt as a photo or PDF.")
    elif state == "ai":
        context.user_data.pop(STATE, None)
        await _ask_ai(update, text)
    else:
        await msg.reply_text("Choose an option from the menu.", reply_markup=_menu())


async def _media(update, context):
    if context.user_data.get(STATE) == "payment":
        await _submit_receipt(update, context)
    elif update.effective_message:
        await update.effective_message.reply_text("Choose an option from the menu.", reply_markup=_menu())


async def _error(update, context):
    logger.error("Unhandled Telegram bot error", exc_info=context.error)


def build_application():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(_callbacks))
    app.add_handler(MessageHandler(filters.CONTACT, _contact))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, _media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text))
    app.add_error_handler(_error)
    return app


async def start_polling_bot():
    """Start polling inside an existing asyncio application lifecycle."""
    application = build_application()
    initialized = False
    try:
        await application.initialize()
        initialized = True
        await application.start()
        if application.updater is None:
            raise RuntimeError("Telegram polling updater is unavailable.")
        await application.updater.start_polling()
        application.bot_data["notification_task"] = asyncio.create_task(
            run_notification_worker(application.bot)
        )
    except Exception:
        logger.exception("Telegram bot startup failed")
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        if initialized:
            await application.shutdown()
        raise
    logger.info("Marina Smile Telegram polling started")
    return application


async def stop_polling_bot(application: Application) -> None:
    """Stop Telegram polling gracefully during FastAPI shutdown."""
    task = application.bot_data.get("notification_task")
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if application.updater and application.updater.running:
        await application.updater.stop()
    if application.running:
        await application.stop()
    await application.shutdown()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("Starting Marina Smile Telegram bot polling")
    build_application().run_polling()


if __name__ == "__main__":
    main()
