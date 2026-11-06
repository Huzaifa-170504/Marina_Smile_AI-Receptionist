from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
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
from app.operations.slot_finder import find_available_slots
from app.repositories.clinics import get_active_clinic
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.repositories.services import list_active_services

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logger = logging.getLogger(__name__)
STATE = "marina_state"
BUCKET = "payment-proofs"
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
MENU = (("@ Book appointment", "@ See services"), ("@ Booking status", "@ Cancel booking"),
        ("@ Uploaded payments", "@ Ask MarinaSmileAI"), ("@ Main menu",))


def _menu():
    return ReplyKeyboardMarkup([[KeyboardButton(item) for item in row] for row in MENU],
                               resize_keyboard=True, is_persistent=True)


def _client():
    return get_supabase_client()


def _clinic_id():
    return str(get_settings().default_clinic_id)


def _phone_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normal_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


async def _private(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type == "private":
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Please use this bot in a private chat for patient privacy.")
    return False


def _patient_record(phone: str, full_name: str) -> dict | None:
    client, clinic = _client(), _clinic_id()
    contacts = client.table("contacts").select("id,phone_number").eq("clinic_id", clinic).execute().data or []
    contact_ids = [r["id"] for r in contacts if r.get("phone_number") and _phone_digits(r["phone_number"]) == phone]
    if len(contact_ids) != 1:
        return None
    patients = client.table("patients").select("id,full_name").eq("clinic_id", clinic).eq(
        "primary_contact_id", contact_ids[0]).eq("active", True).execute().data or []
    matches = [r for r in patients if _normal_name(r["full_name"]) == _normal_name(full_name)]
    if len(matches) != 1:
        return None
    return {"clinic_id": clinic, "contact_id": contact_ids[0], "patient_id": matches[0]["id"],
            "name": matches[0]["full_name"]}


async def _require_patient(update, context):
    patient = context.user_data.get("patient")
    if patient:
        return patient
    await update.effective_message.reply_text(
        "Share your registered phone number to verify your patient record.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Share registered phone", request_contact=True)]],
                                         resize_keyboard=True, one_time_keyboard=True))
    context.user_data[STATE] = "phone"
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _private(update):
        context.user_data.pop(STATE, None)
        await update.effective_message.reply_text("Welcome to Marina Smile. Choose an option:",
                                                   reply_markup=_menu())


async def _contact(update, context):
    if not await _private(update):
        return
    contact = update.effective_message.contact
    if not contact or not update.effective_user or contact.user_id != update.effective_user.id or not contact.phone_number:
        await update.effective_message.reply_text("Please share your own phone using the button.")
        return
    context.user_data["verified_phone"] = _phone_digits(contact.phone_number)
    context.user_data[STATE] = "name"
    await update.effective_message.reply_text("Enter your full name exactly as registered with the clinic.")


async def _verify_name(update, context):
    try:
        patient = await asyncio.to_thread(_patient_record, context.user_data.pop("verified_phone", ""),
                                          update.effective_message.text.strip())
    except Exception:
        logger.exception("Telegram patient lookup failed")
        await update.effective_message.reply_text("I couldn't verify your record right now. Please try later.")
        return
    context.user_data.pop(STATE, None)
    if not patient:
        await update.effective_message.reply_text("No unique active patient matched that phone and name. Please contact reception.",
                                                   reply_markup=_menu())
        return
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


async def _begin_booking(update, context):
    patient = await _require_patient(update, context)
    if not patient:
        return
    try:
        services = await asyncio.to_thread(list_active_services, patient["clinic_id"])
    except Exception:
        logger.exception("Telegram booking services lookup failed")
        await update.effective_message.reply_text("Booking is temporarily unavailable.")
        return
    if not services:
        await update.effective_message.reply_text("No active services are available to book.")
        return
    context.user_data["booking"] = {}
    await update.effective_message.reply_text("Choose a service:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(s.name, callback_data=f"service:{s.id}")]
                                           for s in services]))


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
    if data.startswith("service:"):
        service_id = data.removeprefix("service:")
        services = await asyncio.to_thread(list_active_services, patient["clinic_id"])
        service = next((s for s in services if s.id == service_id), None)
        if not service:
            await query.edit_message_text("That service is no longer available. Start booking again.")
            return
        details = await asyncio.to_thread(lambda: client.table("services").select(
            "booking_deposit_amount,currency,payment_required").eq("clinic_id", patient["clinic_id"]).eq(
            "id", service_id).eq("active", True).single().execute().data)
        dentists = await asyncio.to_thread(list_active_dentists, patient["clinic_id"])
        dentists = await asyncio.to_thread(filter_dentists_for_service, patient["clinic_id"], dentists, service_id)
        if not dentists:
            await query.edit_message_text("No active doctors are assigned to this service.")
            return
        context.user_data["booking"] = {"service_id": service_id, "service_name": service.name,
            "deposit": details.get("booking_deposit_amount"), "currency": details.get("currency") or "AED",
            "payment_required": details.get("payment_required", True),
            "dentists": {d.id: d.name for d in dentists}}
        await query.edit_message_text(f"Choose an available doctor for {service.name}:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(d.name, callback_data=f"doctor:{d.id}")]
                                               for d in dentists]))
    elif data.startswith("doctor:"):
        booking, doctor_id = context.user_data.get("booking", {}), data.removeprefix("doctor:")
        if doctor_id not in booking.get("dentists", {}):
            await query.edit_message_text("That doctor selection expired. Please start booking again.")
            return
        booking["dentist_id"] = doctor_id
        context.user_data[STATE] = "date"
        await query.edit_message_text(f"Selected {booking['dentists'][doctor_id]}. Send a date as YYYY-MM-DD.")
    elif data.startswith("slot:"):
        try:
            slot = context.user_data["slots"][int(data.removeprefix("slot:"))]
        except (KeyError, ValueError, IndexError):
            await query.edit_message_text("That time choice expired. Please check availability again.")
            return
        booking = context.user_data["booking"]
        request_id = str(uuid4())
        status = "awaiting_payment" if booking.get("payment_required", True) else "awaiting_slot_selection"
        try:
            await asyncio.to_thread(lambda: client.table("booking_requests").insert({
                "id": request_id, "clinic_id": patient["clinic_id"], "patient_id": patient["patient_id"],
                "contact_id": patient["contact_id"], "service_id": booking["service_id"],
                "preferred_dentist_id": booking["dentist_id"], "preferred_date": slot.start_at.date().isoformat(),
                "preferred_window_start": slot.start_at.isoformat(), "preferred_window_end": slot.end_at.isoformat(),
                "status": status, "idempotency_key": str(uuid4()),
            }).execute())
        except Exception:
            logger.exception("Telegram booking request insert failed")
            await query.edit_message_text("I couldn't save the booking request. Please try again.")
            return
        context.user_data.pop("slots", None)
        if not booking.get("payment_required", True):
            context.user_data.pop(STATE, None)
            await query.edit_message_text("Your booking request is saved. The clinic will confirm the appointment.")
            return
        context.user_data["request_id"] = request_id
        context.user_data[STATE] = "payment"
        amount = booking.get("deposit")
        due = f"{booking['currency']} {amount}" if amount is not None else "the amount confirmed by reception"
        await query.edit_message_text(f"Request saved for {slot.start_at:%Y-%m-%d %H:%M}. Upload your receipt for {due}. "
                                      "The clinic will verify payment before confirming your appointment.")
    elif data.startswith("cancel-request:"):
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
    elif data.startswith("cancel-appointment:"):
        try:
            result = await asyncio.to_thread(cancel_appointment, patient["clinic_id"], patient["contact_id"],
                data.removeprefix("cancel-appointment:"),
                actor=AppointmentActor(actor_type=AppointmentActorType.PATIENT, actor_id=patient["patient_id"]))
            ok = result.status.value in {"CANCELLED", "ALREADY_CANCELLED"}
            await query.edit_message_text("Appointment cancelled." if ok else (result.reason or "The appointment could not be cancelled."))
        except Exception:
            logger.exception("Telegram appointment cancellation failed")
            await query.edit_message_text("The appointment could not be cancelled right now.")


async def _find_slots(update, context):
    msg = update.effective_message
    try:
        requested_date = date.fromisoformat(msg.text.strip())
        patient, booking = context.user_data["patient"], context.user_data["booking"]
        clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
        tz = ZoneInfo(clinic.timezone)
        start = datetime.combine(requested_date, time.min, tzinfo=tz)
        result = await asyncio.to_thread(find_available_slots, patient["clinic_id"], booking["dentist_id"],
                                         booking["service_id"], start, start + timedelta(days=1), 20)
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
    context.user_data.pop(STATE, None)
    await msg.reply_text("Receipt uploaded. Status: payment submitted, awaiting clinic verification.", reply_markup=_menu())


def _store_receipt(patient, request_id, payload, mime, extension):
    client, clinic = _client(), patient["clinic_id"]
    request = client.table("booking_requests").select("id,status,service_id").eq("clinic_id", clinic).eq(
        "id", request_id).eq("patient_id", patient["patient_id"]).single().execute().data
    if request["status"] not in {"awaiting_payment", "payment_rejected"}:
        raise ValueError("Booking request is not accepting payment")
    service = client.table("services").select("booking_deposit_amount,currency").eq(
        "clinic_id", clinic).eq("id", request["service_id"]).single().execute().data
    path = f"{clinic}/{patient['patient_id']}/{request_id}/{uuid4()}.{extension}"
    storage = client.storage.from_(BUCKET)
    storage.upload(path, payload, {"content-type": mime, "upsert": "false"})
    payment_id = str(uuid4())
    try:
        client.table("payments").insert({
            "id": payment_id, "clinic_id": clinic, "booking_request_id": request_id,
            "patient_id": patient["patient_id"], "amount_expected": service.get("booking_deposit_amount"),
            "currency": service.get("currency") or "AED", "proof_storage_path": path, "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(), "submission_idempotency_key": str(uuid4()),
        }).execute()
        client.rpc("transition_booking_request", {"p_booking_request_id": request_id,
            "p_expected_status": request["status"], "p_target_status": "payment_submitted"}).execute()
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
        requests = await asyncio.to_thread(lambda: client.table("booking_requests").select(
            "id,status,preferred_date,service_id").eq("clinic_id", patient["clinic_id"]).eq(
            "patient_id", patient["patient_id"]).order("created_at", desc=True).limit(10).execute().data or [])
        appointments = await asyncio.to_thread(lambda: client.table("appointments").select(
            "id,status,start_at,service_id").eq("clinic_id", patient["clinic_id"]).eq(
            "patient_id", patient["patient_id"]).order("start_at", desc=True).limit(10).execute().data or [])
        services = await asyncio.to_thread(list_active_services, patient["clinic_id"])
        names = {s.id: s.name for s in services}
        lines = [f"• Request {r['id'][:8]} · {names.get(r['service_id'], 'Appointment')} · "
                 f"{r.get('preferred_date') or 'date pending'} · {r['status'].replace('_', ' ')}" for r in requests]
        lines += [f"• Appointment {a['id'][:8]} · {names.get(a['service_id'], 'Appointment')} · "
                  f"{a['start_at']} · {a['status']}" for a in appointments]
        await update.effective_message.reply_text("\n".join(lines) if lines else "No bookings found.", reply_markup=_menu())
    except Exception:
        logger.exception("Telegram booking status lookup failed")
        await update.effective_message.reply_text("Booking status is temporarily unavailable.")


async def _payments(update, patient):
    client = _client()
    try:
        rows = await asyncio.to_thread(lambda: client.table("payments").select(
            "booking_request_id,amount_expected,currency,status,proof_storage_path").eq(
            "clinic_id", patient["clinic_id"]).eq("patient_id", patient["patient_id"]).order(
            "created_at", desc=True).limit(10).execute().data or [])
        lines = ["Your uploaded payments:"]
        for row in rows:
            amount = f"{row.get('currency', 'AED')} {row['amount_expected']}" if row.get("amount_expected") is not None else "amount pending"
            text = f"• {amount} · {row['status']} · request {row['booking_request_id'][:8]}"
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
    if state == "name":
        await _verify_name(update, context)
    elif state == "date":
        await _find_slots(update, context)
    elif state == "payment":
        await msg.reply_text("Upload the payment receipt as a photo or PDF.")
    elif state == "ai":
        context.user_data.pop(STATE, None)
        await _ask_ai(update, text)
    elif text == "@ Main menu":
        context.user_data.pop(STATE, None)
        await msg.reply_text("Main menu", reply_markup=_menu())
    elif text == "@ See services":
        await _services(update)
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
