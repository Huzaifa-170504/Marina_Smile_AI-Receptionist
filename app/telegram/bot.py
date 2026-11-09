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
    lookup_identities,
    normalize_name,
    resolve_or_register_patient,
)
from app.operations.reschedule import reschedule_appointment
from app.operations.slot_finder import find_available_slots
from app.repositories.clinics import get_active_clinic
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.telegram.notifications import run_notification_worker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logger = logging.getLogger(__name__)
STATE = "marina_state"
BUCKET = "payment-proofs"
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
MENU = (
    ("Book Appointment", "My Appointments"),
    ("Our Doctors", "Dental Services"),
    ("Ask Marina AI", "Payments & Pricing"),
    ("Help & Contact Reception",),
)
IDENTIFIER_PROMPT = (
    "Enter the phone number you used to book, in international format "
    "(e.g. +92 311-8917906) or local format (e.g. 03118917906), or your email address."
)


def _menu():
    return ReplyKeyboardMarkup([[KeyboardButton(item) for item in row] for row in MENU],
                               resize_keyboard=True, is_persistent=True)


def _main_menu_row():
    return [InlineKeyboardButton("🏠 Main menu", callback_data="main-menu")]


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


def _patient_profile(patient: dict) -> str:
    age = patient.get("age_at_registration")
    age_text = f"Age: {age}" if age is not None else "Age: not provided"
    gender = patient.get("gender")
    gender_text = f"Gender: {gender}" if gender else "Gender: not provided"
    return f"{patient.get('full_name') or patient.get('name') or 'Patient'} · {age_text} · {gender_text}"


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _private(update):
        _clear_flow(context)
        await update.effective_message.reply_text("Welcome to Marina Smile. Choose an option:",
                                                   reply_markup=_menu())


def _clear_flow(context):
    for key in (
        STATE,
        "booking",
        "slots",
        "patient",
        "booking_identifier",
        "booking_patients",
        "booking_pending",
        "lookup_target",
        "lookup_patients",
        "appointments",
        "reschedule_target",
        "slots",
        "request_id",
        "cancel_status",
        "cancel_appointments",
    ):
        context.user_data.pop(key, None)


async def _contact(update, context):
    if not await _private(update):
        return
    if context.user_data.get(STATE) != "booking_identifier":
        return
    contact = update.effective_message.contact
    if not contact or not update.effective_user or contact.user_id != update.effective_user.id or not contact.phone_number:
        await update.effective_message.reply_text("Please share your own phone using the button.")
        return
    context.user_data["booking_identifier"] = ("phone", _phone_digits(contact.phone_number))
    await _continue_booking_identifier(update, context)


async def _continue_booking_identifier(update, context):
    identifier_type, identifier = context.user_data["booking_identifier"]
    try:
        info = await asyncio.to_thread(
            lookup_identities, _clinic_id(), identifier_type, identifier
        )
    except Exception:
        logger.exception("Telegram patient lookup failed")
        await update.effective_message.reply_text("I couldn't verify your record right now. Please try later.")
        return

    patients = [row for row in info.get("patients", []) if row.get("active", True)]
    if not info.get("contacts"):
        context.user_data[STATE] = "booking_name"
        await update.effective_message.reply_text(
            "No previous record found. Enter your full name to register as a new patient."
        )
        return
    if not patients:
        context.user_data[STATE] = "booking_name"
        await update.effective_message.reply_text(
            "Enter your full name to complete your patient registration."
        )
        return
    if len(patients) > 1:
        context.user_data["booking_patients"] = patients
        context.user_data[STATE] = "booking_name"
        await update.effective_message.reply_text(
            "More than one patient uses this phone/email. Enter the full name of the patient booking."
        )
        return

    patient = {
        "clinic_id": _clinic_id(),
        "contact_id": patients[0]["primary_contact_id"],
        "patient_id": patients[0]["id"],
        "name": patients[0]["full_name"],
        "age_at_registration": patients[0].get("age_at_registration"),
        "gender": patients[0].get("gender"),
    }
    await _maybe_complete_profile(update, context, patient)


async def _verify_booking_identifier(update, context):
    identifier = _normalize_identifier(update.effective_message.text or "")
    if identifier is None:
        await update.effective_message.reply_text(IDENTIFIER_PROMPT)
        return
    context.user_data["booking_identifier"] = identifier
    await _continue_booking_identifier(update, context)


async def _verify_booking_name(update, context):
    name = " ".join((update.effective_message.text or "").split())
    if len(name) < 2:
        await update.effective_message.reply_text("Enter your full name.")
        return

    patients = context.user_data.pop("booking_patients", None)
    if patients:
        matches = [row for row in patients if normalize_name(row["full_name"]) == normalize_name(name)]
        if len(matches) != 1:
            context.user_data["booking_patients"] = patients
            await update.effective_message.reply_text(
                "That name does not match a patient for this phone/email. Enter the full registered name."
            )
            return
        patient = {
            "clinic_id": _clinic_id(),
            "contact_id": matches[0]["primary_contact_id"],
            "patient_id": matches[0]["id"],
            "name": matches[0]["full_name"],
            "age_at_registration": matches[0].get("age_at_registration"),
            "gender": matches[0].get("gender"),
        }
        await _maybe_complete_profile(update, context, patient)
        return

    identifier = context.user_data.get("booking_identifier")
    if not identifier:
        _clear_flow(context)
        await update.effective_message.reply_text("Please start again with @ Book appointment.", reply_markup=_menu())
        return
    context.user_data["booking_pending"] = {
        "identifier": identifier,
        "name": name,
        "patient": None,
        "age": None,
        "gender": None,
        "chat_id": update.effective_chat.id if update.effective_chat else None,
    }
    context.user_data[STATE] = "booking_age"
    await update.effective_message.reply_text("Enter the patient's age in years (for example: 30).")


def _gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Male", callback_data="gender:male"),
            InlineKeyboardButton("Female", callback_data="gender:female"),
            InlineKeyboardButton("Other", callback_data="gender:other"),
        ],
        _main_menu_row(),
    ])


async def _maybe_complete_profile(update, context, patient: dict):
    if patient.get("age_at_registration") is None:
        context.user_data["booking_pending"] = {
            "identifier": context.user_data.get("booking_identifier"),
            "patient": patient,
            "age": None,
            "gender": None,
            "chat_id": update.effective_chat.id if update.effective_chat else None,
        }
        context.user_data[STATE] = "booking_age"
        await update.effective_message.reply_text("Enter the patient's age in years (for example: 30).")
        return
    if not patient.get("gender"):
        context.user_data["booking_pending"] = {
            "identifier": context.user_data.get("booking_identifier"),
            "patient": patient,
            "age": patient.get("age_at_registration"),
            "gender": None,
            "chat_id": update.effective_chat.id if update.effective_chat else None,
        }
        context.user_data[STATE] = "booking_gender"
        await update.effective_message.reply_text(
            "Select the patient's gender:", reply_markup=_gender_keyboard()
        )
        return
    await _after_patient_verified(update, context, patient)


def _persist_profile(pending: dict, gender: str) -> dict:
    patient = pending.get("patient")
    age = pending.get("age")
    if patient:
        updates: dict = {"gender": gender}
        if age is not None:
            updates["age_at_registration"] = age
        _client().table("patients").update(updates).eq(
            "clinic_id", _clinic_id()
        ).eq("id", patient["patient_id"]).execute()
        patient.update(updates)
        return patient
    identifier = pending["identifier"]
    return resolve_or_register_patient(
        _clinic_id(),
        identifier[0],
        identifier[1],
        pending["name"],
        age_at_registration=age,
        gender=gender,
        telegram_chat_id=pending.get("chat_id"),
    )


async def _verify_booking_age(update, context):
    try:
        age = int((update.effective_message.text or "").strip())
    except ValueError:
        await update.effective_message.reply_text("Enter age as a whole number, for example: 30.")
        return
    if not 0 <= age <= 120:
        await update.effective_message.reply_text("Enter a valid age between 0 and 120.")
        return
    pending = context.user_data.get("booking_pending")
    if not pending:
        _clear_flow(context)
        await update.effective_message.reply_text("Please start again with @ Book appointment.", reply_markup=_menu())
        return
    pending["age"] = age
    patient = pending.get("patient")
    if patient and patient.get("gender"):
        await _finish_profile(update, context, patient["gender"])
        return
    context.user_data[STATE] = "booking_gender"
    await update.effective_message.reply_text(
        "Select the patient's gender:", reply_markup=_gender_keyboard()
    )


async def _verify_booking_gender(update, context):
    value = (update.effective_message.text or "").strip().casefold()
    if value not in {"male", "female", "other"}:
        await update.effective_message.reply_text(
            "Enter gender as male, female, or other.", reply_markup=_gender_keyboard()
        )
        return
    await _finish_profile(update, context, value)


async def _finish_profile(update_or_query, context, gender: str):
    pending = context.user_data.get("booking_pending")
    if not pending:
        _clear_flow(context)
        return
    try:
        patient = await asyncio.to_thread(_persist_profile, pending, gender)
    except PatientBookingError as exc:
        context.user_data.pop("booking_pending", None)
        context.user_data.pop(STATE, None)
        method = getattr(update_or_query, "message", update_or_query).reply_text
        await method(str(exc), reply_markup=_menu())
        return
    except Exception:
        logger.exception("Telegram patient registration failed")
        method = getattr(update_or_query, "message", update_or_query).reply_text
        await method("I couldn't save your patient record. Please try later.")
        return
    context.user_data.pop("booking_pending", None)
    context.user_data.pop(STATE, None)
    message = getattr(update_or_query, "message", update_or_query)
    await _after_patient_verified(update_or_query, context, patient, reply_message=message)


async def _after_patient_verified(update, context, patient, reply_message=None):
    context.user_data["patient"] = patient
    message = reply_message or getattr(update, "effective_message", None) or getattr(update, "message", None)
    try:
        existing = await asyncio.to_thread(
            active_booking_for_patient, patient["clinic_id"], patient["patient_id"]
        )
        if existing:
            _clear_flow(context)
            await message.reply_text(describe_active_booking(existing), reply_markup=_menu())
            return
        services = await asyncio.to_thread(_load_service_rows, patient["clinic_id"])
    except Exception:
        logger.exception("Telegram booking services lookup failed")
        await message.reply_text("Booking is temporarily unavailable.")
        return
    if not services:
        await message.reply_text("No active services are available to book.", reply_markup=_menu())
        return
    context.user_data["booking"] = {"services": services, "selected": []}
    context.user_data[STATE] = "booking"
    await message.reply_text(
        "Choose one or more services. Your bill is calculated automatically:",
        reply_markup=_service_keyboard(context.user_data["booking"]),
    )


async def _begin_lookup(update, context, target: str):
    _clear_flow(context)
    context.user_data["lookup_target"] = target
    context.user_data[STATE] = "lookup_identifier"
    message = getattr(update, "effective_message", None) or update.message
    await message.reply_text(IDENTIFIER_PROMPT)


async def _verify_lookup_identifier(update, context):
    identifier = _normalize_identifier(update.effective_message.text or "")
    if identifier is None:
        await update.effective_message.reply_text(IDENTIFIER_PROMPT)
        return
    try:
        info = await asyncio.to_thread(
            lookup_identities, _clinic_id(), identifier[0], identifier[1]
        )
    except Exception:
        logger.exception("Telegram identifier lookup failed")
        await update.effective_message.reply_text("I couldn't look up that phone/email right now. Please try later.")
        return
    target = context.user_data.pop("lookup_target", None)
    context.user_data.pop(STATE, None)
    if not info.get("contacts"):
        message = (
            "You did not pay your bill yet."
            if target == "payments"
            else "No booking was found for that phone number or email."
        )
        await update.effective_message.reply_text(message, reply_markup=_menu())
        return
    patients = info.get("patients", [])
    if target == "status":
        await _status(update, patients)
    elif target == "cancel":
        await _cancel_menu(update, patients, context)
    elif target == "payments":
        await _payments(update, patients)
    elif target == "appointments":
        context.user_data["lookup_patients"] = patients
        await _my_appointments(update, patients, context)


async def _begin_booking(update, context):
    _clear_flow(context)
    context.user_data[STATE] = "booking_identifier"
    await update.effective_message.reply_text(
        IDENTIFIER_PROMPT,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Share my phone", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


async def _service_catalog_text() -> str:
    rows = await asyncio.to_thread(lambda: _client().table("services").select(
        "name,description,duration_minutes,price_from,currency,active").eq(
        "clinic_id", _clinic_id()).eq("active", True).order("name").execute().data or [])
    if not rows:
        return "No active services are listed."
    lines = ["Dental services:"]
    for row in rows:
        price = row.get("price_from")
        price_text = f" · from {row.get('currency') or 'AED'} {price}" if price is not None else ""
        detail = f" · {row.get('description')}" if row.get("description") else ""
        lines.append(f"• {row['name']}{price_text} · {row['duration_minutes']} min{detail}")
    return "\n".join(lines)


async def _services(update):
    try:
        await update.effective_message.reply_text(await _service_catalog_text(), reply_markup=_menu())
    except Exception:
        logger.exception("Telegram service listing failed")
        await update.effective_message.reply_text("Services are temporarily unavailable.")


async def _doctors(update):
    def load():
        client, clinic = _client(), _clinic_id()
        return client.table("dentists").select(
            "id,name,specialty,dentist_services(services(name))"
        ).eq("clinic_id", clinic).eq("active", True).order("name").execute().data or []

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
    rows.append(_main_menu_row())
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
    rows.append(_main_menu_row())
    return InlineKeyboardMarkup(rows)


def _payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cash in hand", callback_data="mode:cash")],
        [InlineKeyboardButton("Online payment", callback_data="mode:online")],
        _main_menu_row(),
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm booking", callback_data="confirm-booking")],
        [InlineKeyboardButton("Change payment method", callback_data="change-mode")],
        _main_menu_row(),
    ])


def _current_service(booking: dict) -> dict | None:
    order = booking.get("service_order") or []
    index = booking.get("schedule_index", 0)
    if index >= len(order):
        return None
    return booking.get("services", {}).get(order[index])


def _schedule_lines(booking: dict, timezone_name: str) -> list[str]:
    rows = []
    for service_id in booking.get("service_order") or []:
        service = booking.get("services", {}).get(service_id) or {}
        scheduled = (booking.get("schedule") or {}).get(service_id) or {}
        rows.append(
            f"• {service.get('name', 'Service')} · "
            f"{_when_text(scheduled.get('start_at'), timezone_name)}"
        )
    return rows


async def _review_text(context, patient: dict) -> tuple[str, dict]:
    booking = context.user_data["booking"]
    services, total, _duration = _selected_summary(booking)
    clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
    mode = booking.get("payment_mode")
    mode_text = "Cash in hand · unpaid" if mode == "cash" else "Online payment · awaiting receipt"
    doctor = booking.get("dentists", {}).get(booking.get("dentist_id"), "—")
    schedule = _schedule_lines(booking, clinic.timezone)
    text = (
        "Review your booking before confirming:\n\n"
        f"Doctor: {doctor}\n"
        + "\n".join(schedule)
        + f"\n\nTotal bill: {_money(total, booking.get('currency') or 'AED')}\n"
        f"Payment: {mode_text}\n\n"
        "⚠️ You cannot edit the selected services once you confirm."
    )
    return text, {"services": services, "total": total}


async def _callbacks(update, context):
    if not await _private(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "main-menu":
        _clear_flow(context)
        await query.message.reply_text("Main menu", reply_markup=_menu())
        return

    if data.startswith("pay:"):
        action = data.removeprefix("pay:")
        if action == "prices":
            try:
                await query.edit_message_text(await _service_catalog_text())
            except Exception:
                logger.exception("Telegram price listing failed")
                await query.edit_message_text("Prices are temporarily unavailable.")
        elif action == "methods":
            await query.edit_message_text(
                "Accepted payment methods:\n"
                "• Cash in hand at the clinic. The booking shows unpaid until reception receives payment.\n"
                "• Online payment by uploading a receipt during booking. Clinic staff verify it manually.\n\n"
                "No card details are collected in Telegram."
            )
        elif action == "history":
            await _begin_lookup(query, context, "payments")
        elif action == "status":
            await _begin_lookup(query, context, "status")
        elif action == "refund":
            await query.edit_message_text(
                "Refund requests are reviewed by clinic reception. Eligibility depends on the "
                "clinic's refund policy and the payment status. Use Help & Contact Reception to request a review."
            )
        return

    if data == "my-appointments":
        patients = context.user_data.get("lookup_patients") or []
        await _my_appointments(query, patients, context)
        return

    if data.startswith("appt-view:"):
        row = context.user_data.get("appointments", {}).get(data.removeprefix("appt-view:"))
        if not row:
            await query.edit_message_text("That appointment is no longer available.")
            return
        clinic = await asyncio.to_thread(get_active_clinic, _clinic_id())
        patients = context.user_data.get("lookup_patients") or []
        profile = next(
            (_patient_profile(item) for item in patients if item["id"] == row.get("patient_id")),
            "",
        )
        text = "\n".join(
            [f"👤 {profile}", ""] if profile else []
        ) + "\n".join(_appointment_detail_lines(row, clinic.timezone))
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Back to appointments", callback_data="my-appointments")
            ]]),
        )
        return

    if data.startswith("appt-cancel:"):
        row = context.user_data.get("appointments", {}).get(data.removeprefix("appt-cancel:"))
        if not row:
            await query.edit_message_text("That appointment is no longer available.")
            return
        reference = row.get("appointment_reference") or row["id"][:8]
        await query.edit_message_text(
            f"Cancel appointment {reference}? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, cancel", callback_data=f"appt-cancel-confirm:{row['id']}")],
                [InlineKeyboardButton("Back to appointments", callback_data="my-appointments")],
            ]),
        )
        return

    if data.startswith("appt-cancel-confirm:"):
        row = context.user_data.get("appointments", {}).get(data.removeprefix("appt-cancel-confirm:"))
        if not row:
            await query.edit_message_text("That appointment is no longer available.")
            return
        outcomes = await _cancel_stored_appointments([row])
        ok = bool(outcomes and outcomes[0][1])
        reference = row.get("appointment_reference") or row["id"][:8]
        await query.edit_message_text(
            f"{reference} cancelled." if ok else (outcomes[0][2] if outcomes else "The appointment could not be cancelled.")
        )
        return

    if data.startswith("plan-cancel:"):
        request_id = data.removeprefix("plan-cancel:")
        rows = [
            row for row in (context.user_data.get("appointments") or {}).values()
            if row.get("booking_request_id") == request_id
        ]
        if not rows:
            await query.edit_message_text("That booking plan is no longer available.")
            return
        reference = rows[0].get("appointment_reference") or rows[0]["id"][:8]
        await query.edit_message_text(
            f"Cancel the entire booking plan {reference} ({len(rows)} appointment(s))? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, cancel plan", callback_data=f"plan-cancel-confirm:{request_id}")],
                [InlineKeyboardButton("Back to appointments", callback_data="my-appointments")],
            ]),
        )
        return

    if data.startswith("plan-cancel-confirm:"):
        request_id = data.removeprefix("plan-cancel-confirm:")
        rows = [
            row for row in (context.user_data.get("appointments") or {}).values()
            if row.get("booking_request_id") == request_id
        ]
        outcomes = await _cancel_stored_appointments(rows)
        cancelled = sum(1 for _row, ok, _reason in outcomes if ok)
        failures = [reason for _row, ok, reason in outcomes if not ok and reason]
        text = f"Cancelled {cancelled} of {len(rows)} appointment(s)."
        if failures:
            text += "\n" + "\n".join(failures)
        await query.edit_message_text(text)
        return

    if data.startswith("appt-reschedule:"):
        row = context.user_data.get("appointments", {}).get(data.removeprefix("appt-reschedule:"))
        if not row:
            await query.edit_message_text("That appointment is no longer available.")
            return
        target = _reschedule_target(row)
        context.user_data["reschedule_target"] = target
        context.user_data[STATE] = "reschedule_date"
        await query.edit_message_text(
            f"Send the new date for {target['reference']} as YYYY-MM-DD."
        )
        return

    if data.startswith("resched-slot:"):
        target = context.user_data.get("reschedule_target")
        try:
            slot = context.user_data["slots"][int(data.removeprefix("resched-slot:"))]
        except (KeyError, TypeError, ValueError, IndexError):
            await query.edit_message_text("That time choice expired. Please start the reschedule again.")
            return
        if not target:
            await query.edit_message_text("Please choose an appointment to reschedule.")
            return
        try:
            result = await asyncio.to_thread(
                reschedule_appointment,
                target["clinic_id"],
                target["contact_id"],
                target["appointment_id"],
                slot.start_at,
                actor=AppointmentActor(
                    actor_type=AppointmentActorType.PATIENT,
                    actor_id=target["patient_id"],
                ),
                new_dentist_id=target["dentist_id"],
                duration_minutes_override=target["duration_minutes"],
                required_service_ids=target["service_ids"],
                service_name_override=target["service_names"],
            )
            if result.status.value == "RESCHEDULED":
                old_start = _parse_ts(target.get("old_start_at"))
                stored_row = (context.user_data.get("appointments") or {}).get(target["appointment_id"])
                if old_start and stored_row:
                    await asyncio.to_thread(
                        _shift_appointment_service_times,
                        stored_row,
                        slot.start_at - old_start,
                    )
                clinic = await asyncio.to_thread(get_active_clinic, target["clinic_id"])
                await query.edit_message_text(
                    f"{target['reference']} rescheduled to "
                    f"{_when_text(slot.start_at.isoformat(), clinic.timezone)}."
                )
            else:
                await query.edit_message_text(
                    result.reason or "The appointment could not be rescheduled."
                )
        except Exception:
            logger.exception("Telegram appointment reschedule failed")
            await query.edit_message_text("The appointment could not be rescheduled right now.")
        finally:
            context.user_data.pop("reschedule_target", None)
            context.user_data.pop("slots", None)
            context.user_data.pop(STATE, None)
        return

    if data.startswith("cancel-request:"):
        request_id = data.removeprefix("cancel-request:")
        expected = context.user_data.get("cancel_status", {}).get(request_id)
        if not expected:
            await query.edit_message_text("That booking request is no longer available to cancel.")
            return
        try:
            await asyncio.to_thread(lambda: _client().rpc("transition_booking_request", {
                "p_booking_request_id": request_id, "p_expected_status": expected,
                "p_target_status": "cancelled"}).execute())
            await query.edit_message_text("Booking request cancelled.")
        except Exception:
            logger.exception("Telegram booking request cancellation failed")
            await query.edit_message_text("The request could not be cancelled. Check its current status.")
        return

    if data.startswith("cancel-appointment:"):
        appointment_id = data.removeprefix("cancel-appointment:")
        stored = context.user_data.get("cancel_appointments", {}).get(appointment_id)
        if not stored:
            await query.edit_message_text("That appointment is no longer available to cancel.")
            return
        try:
            result = await asyncio.to_thread(
                cancel_appointment,
                stored["clinic_id"],
                stored["contact_id"],
                appointment_id,
                actor=AppointmentActor(actor_type=AppointmentActorType.PATIENT, actor_id=stored["patient_id"]),
            )
            ok = result.status.value in {"CANCELLED", "ALREADY_CANCELLED"}
            if ok:
                await asyncio.to_thread(
                    lambda: _client().table("outbound_notifications").update({"status": "cancelled"})
                    .eq("clinic_id", stored["clinic_id"])
                    .eq("appointment_id", appointment_id)
                    .eq("status", "pending").execute()
                )
                if stored.get("booking_request_id"):
                    try:
                        await asyncio.to_thread(
                            _cancel_linked_booking_request,
                            _client(),
                            stored["booking_request_id"],
                        )
                    except Exception:
                        logger.exception("Booking request cancellation failed after appointment cancel")
            await query.edit_message_text("Appointment cancelled." if ok else (result.reason or "The appointment could not be cancelled."))
        except Exception:
            logger.exception("Telegram appointment cancellation failed")
            await query.edit_message_text("The appointment could not be cancelled right now.")
        return

    if data.startswith("gender:"):
        gender = data.removeprefix("gender:")
        if gender not in {"male", "female", "other"}:
            await query.edit_message_text("Select male, female, or other.")
            return
        await query.edit_message_text(f"Gender: {gender}. Saving your details…")
        await _finish_profile(query, context, gender)
        return

    patient = context.user_data.get("patient")
    if not patient:
        await query.edit_message_text("Please start again with @ Book appointment.")
        return

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
        booking["schedule"] = {}
        booking["service_order"] = list(booking.get("selected") or [])
        booking["schedule_index"] = 0
        context.user_data[STATE] = "date"
        service = _current_service(booking)
        await query.edit_message_text(
            f"Selected {booking['dentists'][doctor_id]}.\n"
            f"Send the date for {service['name']} as YYYY-MM-DD.",
            reply_markup=InlineKeyboardMarkup([_main_menu_row()]),
        )
        return

    if data.startswith("slot:"):
        booking = context.user_data.get("booking") or {}
        try:
            slot = context.user_data["slots"][int(data.removeprefix("slot:"))]
        except (KeyError, ValueError, IndexError):
            await query.edit_message_text("That time choice expired. Please check availability again.")
            return
        service = _current_service(booking)
        if not service:
            await query.edit_message_text("Your booking details expired. Please start again with @ Book appointment.")
            return
        booking.setdefault("schedule", {})[service["id"]] = {
            "start_at": slot.start_at.isoformat(),
            "end_at": slot.end_at.isoformat(),
        }
        booking["schedule_index"] = booking.get("schedule_index", 0) + 1
        context.user_data.pop("slots", None)
        next_service = _current_service(booking)
        if next_service:
            context.user_data[STATE] = "date"
            await query.edit_message_text(
                f"Saved {service['name']}.\n"
                f"Send the date for {next_service['name']} as YYYY-MM-DD "
                "(it will be scheduled after the previous service).",
                reply_markup=InlineKeyboardMarkup([_main_menu_row()]),
            )
            return
        context.user_data.pop(STATE, None)
        services, total, _duration = _selected_summary(booking)
        clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
        schedule_text = "\n".join(_schedule_lines(booking, clinic.timezone))
        await query.edit_message_text(
            f"{schedule_text}\n\nTotal bill: {_money(total, booking.get('currency') or 'AED')}\n"
            "How would you like to pay?\n"
            "• Cash in hand: confirmed immediately as unpaid, paid at the clinic.\n"
            "• Online payment: upload a receipt for clinic verification.",
            reply_markup=_payment_keyboard(),
        )
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
        await query.edit_message_text("How would you like to pay?", reply_markup=_payment_keyboard())
        return

    if data == "confirm-booking":
        booking = context.user_data.get("booking") or {}
        if not booking.get("selected") or not booking.get("dentist_id") or not booking.get("schedule"):
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

        services = []
        for service_id in booking.get("service_order") or []:
            service = booking.get("services", {}).get(service_id) or {}
            scheduled = (booking.get("schedule") or {}).get(service_id) or {}
            services.append({
                "service_id": service_id,
                "name": service.get("name"),
                "price_amount": float(service.get("price_from") or 0),
                "duration_minutes": service.get("duration_minutes"),
                "currency": service.get("currency") or "AED",
                "dentist_id": booking.get("dentist_id"),
                "start_at": scheduled.get("start_at"),
                "end_at": scheduled.get("end_at"),
            })
        starts = [_parse_ts(item["start_at"]) for item in services]
        ends = [_parse_ts(item["end_at"]) for item in services]
        starts = [value for value in starts if value is not None]
        ends = [value for value in ends if value is not None]
        if not starts or not ends:
            await query.edit_message_text("The selected times expired. Please start booking again.")
            return
        start_at, end_at = min(starts), max(ends)
        total = sum(item["price_amount"] for item in services)
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
                await query.message.reply_text(
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
        await query.message.reply_text(
            f"Booking request saved. Total bill: {_money(total, booking.get('currency') or 'AED')}.\n"
            "Upload your receipt for this bill. The clinic verifies online payments before confirming.",
        )
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
    schedule = "\n".join(
        f"• {line.get('service_name')} · {_when_text(line.get('start_at'), clinic.timezone)}"
        for line in services
    )
    return (
        "Appointment confirmed\n\n"
        f"Reference: {appointment.get('appointment_reference') or '—'}\n"
        f"When: {when}\n"
        f"Doctor: {result.get('dentist_name') or '—'}\n"
        f"Services:\n{schedule}\n"
        f"Bill: {_money(appointment.get('total_amount') if appointment.get('total_amount') is not None else request.get('total_amount'), appointment.get('currency') or request.get('currency') or 'AED')}\n"
        f"Payment: {mode} · {payment_status}\n"
        f"Status: {appointment.get('status') or 'confirmed'}"
    )


async def _find_slots(update, context):
    msg = update.effective_message
    try:
        requested_date = date.fromisoformat(msg.text.strip())
        patient, booking = context.user_data["patient"], context.user_data["booking"]
        service = _current_service(booking)
        if not service:
            context.user_data.pop(STATE, None)
            await msg.reply_text("Your service selection expired. Please start booking again.", reply_markup=_menu())
            return
        clinic = await asyncio.to_thread(get_active_clinic, patient["clinic_id"])
        tz = ZoneInfo(clinic.timezone)
        day_start = datetime.combine(requested_date, time.min, tzinfo=tz)
        previous_ends = [
            _parse_ts(value.get("end_at"))
            for value in (booking.get("schedule") or {}).values()
        ]
        previous_ends = [value for value in previous_ends if value is not None]
        window_start = max([day_start, *previous_ends])
        result = await asyncio.to_thread(
            find_available_slots,
            patient["clinic_id"],
            booking["dentist_id"],
            None,
            window_start,
            window_start + timedelta(days=1),
            20,
            duration_override_minutes=int(service["duration_minutes"]),
        )
    except ValueError:
        await msg.reply_text("Send the date as YYYY-MM-DD.")
        return
    except Exception:
        logger.exception("Telegram availability search failed")
        await msg.reply_text("I couldn't check availability right now. Please try later.")
        return
    if not result.slots:
        await msg.reply_text("No available times for that doctor from this date. Send another date.")
        return
    context.user_data["slots"] = result.slots
    rows = [
        [InlineKeyboardButton(
            slot.start_at.astimezone(tz).strftime("%a %d %b, %I:%M %p"),
            callback_data=f"slot:{i}",
        )]
        for i, slot in enumerate(result.slots)
    ]
    rows.append(_main_menu_row())
    await msg.reply_text(f"Choose a time for {service['name']}:", reply_markup=InlineKeyboardMarkup(rows))


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


async def _status(update, patients: list[dict]):
    patient_ids = [row["id"] for row in patients]
    if not patient_ids:
        await update.effective_message.reply_text("No booking was found for that phone number or email.",
                                                   reply_markup=_menu())
        return
    client = _client()
    try:
        clinic = await asyncio.to_thread(get_active_clinic, _clinic_id())
        requests = await asyncio.to_thread(lambda: client.table("booking_requests").select(
            "id,status,preferred_date,preferred_dentist_id,preferred_window_start,preferred_window_end,"
            "service_id,total_amount,currency,payment_mode,payment_status,created_at,"
            "booking_request_services(service_name,start_at,end_at),dentists(name)").eq(
            "clinic_id", _clinic_id()).in_("patient_id", patient_ids).order("created_at", desc=True).limit(10).execute().data or [])
        request_notes = []
        for request in requests:
            if request["status"] in {"payment_verified", "awaiting_slot_selection"}:
                try:
                    result = await asyncio.to_thread(
                        finalize_booking_request, _clinic_id(), request["id"]
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
            "payment_mode,payment_status,dentists(name),appointment_services(service_name,start_at,end_at),"
            "appointment_visits(queue_status)").eq(
            "clinic_id", _clinic_id()).in_("patient_id", patient_ids).order("start_at", desc=True).limit(10).execute().data or [])

        if request_notes:
            await update.effective_message.reply_text("\n".join(request_notes))
        lines = [f"👤 {_patient_profile(row)}" for row in patients]
        if lines:
            lines.append("")
        for request in requests:
            services = _service_names(request.get("booking_request_services") or [])
            doctor = (request.get("dentists") or {}).get("name") or "doctor pending"
            when = _when_text(request.get("preferred_window_start"), clinic.timezone)
            lines.append(
                f"• Request {request['id'][:8]} · {services} · {when} · {doctor} · "
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
        has_bookings = any(line.startswith("• ") for line in lines)
        if not has_bookings:
            lines.append("No bookings found.")
        await update.effective_message.reply_text("\n".join(lines), reply_markup=_menu())
    except Exception:
        logger.exception("Telegram booking status lookup failed")
        await update.effective_message.reply_text("Booking status is temporarily unavailable.")


async def _payments(update, patients: list[dict]):
    patient_ids = [row["id"] for row in patients]
    if not patient_ids:
        await update.effective_message.reply_text("You did not pay your bill yet.", reply_markup=_menu())
        return
    profile_lines = [f"👤 {_patient_profile(row)}" for row in patients]
    client = _client()
    try:
        rows = await asyncio.to_thread(lambda: client.table("payments").select(
            "booking_request_id,amount_expected,currency,status,method,proof_storage_path").eq(
            "clinic_id", _clinic_id()).in_("patient_id", patient_ids).order(
            "created_at", desc=True).limit(10).execute().data or [])
        paid = [row for row in rows if row["status"] == "verified"]
        if not paid:
            pending = any(row["status"] in {"submitted", "awaiting"} for row in rows)
            message = "\n".join(profile_lines + ["", "You did not pay your bill yet."])
            if pending:
                message += "\nYour uploaded receipt is awaiting clinic verification."
            await update.effective_message.reply_text(message, reply_markup=_menu())
            return
        lines = profile_lines + ["", "Your paid bills:"]
        for row in paid:
            amount = _money(row.get("amount_expected"), row.get("currency") or "AED")
            text = f"• {amount} · {row.get('method') or 'online'} · paid · request {row['booking_request_id'][:8]}"
            path = row.get("proof_storage_path")
            if path:
                signed = await asyncio.to_thread(lambda p=path: client.storage.from_(BUCKET).create_signed_url(p, 300))
                url = signed.get("signedURL") or signed.get("signedUrl")
                if url:
                    text += f"\n  Receipt: {url}"
            elif (row.get("method") or "online") == "cash":
                text += "\n  Cash payment recorded at the clinic (no online receipt)."
            lines.append(text)
        await update.effective_message.reply_text("\n".join(lines), reply_markup=_menu(),
                                                   disable_web_page_preview=True)
    except Exception:
        logger.exception("Telegram payment listing failed")
        await update.effective_message.reply_text("Uploaded payments are temporarily unavailable.")


async def _cancel_menu(update, patients: list[dict], context):
    patient_ids = [row["id"] for row in patients]
    profile_text = "\n".join(f"👤 {_patient_profile(row)}" for row in patients)
    if not patient_ids:
        await update.effective_message.reply_text("No booking was found for that phone number or email.",
                                                   reply_markup=_menu())
        return
    client = _client()
    try:
        requests = await asyncio.to_thread(lambda: client.table("booking_requests").select("id,status").eq(
            "clinic_id", _clinic_id()).in_("patient_id", patient_ids).in_("status", [
            "awaiting_patient_info", "awaiting_payment", "payment_submitted", "payment_rejected",
            "payment_verified", "awaiting_slot_selection"]).limit(10).execute().data or [])
        appointments = await asyncio.to_thread(lambda: client.table("appointments").select(
            "id,start_at,contact_id,patient_id,booking_request_id").eq(
            "clinic_id", _clinic_id()).in_("patient_id", patient_ids).in_(
            "status", ["pending", "confirmed"]).limit(10).execute().data or [])
        context.user_data["cancel_status"] = {r["id"]: r["status"] for r in requests}
        context.user_data["cancel_appointments"] = {
            a["id"]: {
                "clinic_id": _clinic_id(),
                "contact_id": a["contact_id"],
                "patient_id": a["patient_id"],
                "booking_request_id": a.get("booking_request_id"),
            }
            for a in appointments
        }
        buttons = [[InlineKeyboardButton(f"Request {r['id'][:8]} · {r['status'].replace('_', ' ')}",
                                          callback_data=f"cancel-request:{r['id']}")] for r in requests]
        buttons += [[InlineKeyboardButton(f"Appointment {a['id'][:8]} · {a['start_at'][:16]}",
                                           callback_data=f"cancel-appointment:{a['id']}")] for a in appointments]
        heading = "Choose a booking to cancel:" if buttons else "No active bookings to cancel."
        await update.effective_message.reply_text(f"{profile_text}\n\n{heading}",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else _menu())
    except Exception:
        logger.exception("Telegram cancellation lookup failed")
        await update.effective_message.reply_text("Your bookings are temporarily unavailable.")


async def _payments_menu(update):
    await update.effective_message.reply_text(
        "Payments & Pricing:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Treatment prices", callback_data="pay:prices")],
            [InlineKeyboardButton("Accepted payment methods", callback_data="pay:methods")],
            [InlineKeyboardButton("My payment history", callback_data="pay:history")],
            [InlineKeyboardButton("Appointment payment status", callback_data="pay:status")],
            [InlineKeyboardButton("Refund policy", callback_data="pay:refund")],
        ]),
    )


async def _help(update):
    def load():
        return _client().table("clinics").select(
            "name,address,emergency_phone,languages"
        ).eq("id", _clinic_id()).limit(1).execute().data or []

    try:
        rows = await asyncio.to_thread(load)
    except Exception:
        logger.exception("Telegram clinic contact lookup failed")
        await update.effective_message.reply_text("Clinic contact details are temporarily unavailable.")
        return
    if not rows:
        await update.effective_message.reply_text("Please contact reception for assistance.", reply_markup=_menu())
        return
    clinic = rows[0]
    lines = [
        f"{clinic['name']} — Help & Contact Reception",
        f"Address: {clinic.get('address') or 'Please ask reception'}",
        f"Emergency phone: {clinic.get('emergency_phone') or 'Please ask reception'}",
        "",
        "For booking changes, payments, refunds, or a human follow-up, reply here and reception will assist.",
        "Your request will be reviewed by clinic staff.",
    ]
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_menu())


def _appointment_service_rows(row: dict) -> list[dict]:
    return row.get("appointment_services") or []


def _appointment_detail_lines(row: dict, timezone_name: str) -> list[str]:
    services = _appointment_service_rows(row)
    service_lines = []
    for line in services:
        when = _when_text(line.get("start_at") or row.get("start_at"), timezone_name)
        service_lines.append(f"   • {line.get('service_name')} · {when}")
    if not service_lines:
        service_lines = [
            f"   • Appointment · {_when_text(row.get('start_at'), timezone_name)}"
        ]
    doctor = (row.get("dentists") or {}).get("name") or "—"
    return [
        f"📋 {row.get('appointment_reference') or row['id'][:8]} · {row['status'].replace('_', ' ')}",
        f"   Doctor: {doctor}",
        *service_lines,
        f"   Bill: {_money(row.get('total_amount'), row.get('currency') or 'AED')}",
        f"   Payment: {row.get('payment_mode') or 'not selected'} · {row.get('payment_status') or 'unpaid'}",
    ]


def _load_patient_appointments(patient_ids: list[str]) -> list[dict]:
    return _client().table("appointments").select(
        "id,appointment_reference,booking_request_id,contact_id,patient_id,status,"
        "start_at,end_at,service_id,dentist_id,google_calendar_event_id,total_amount,"
        "currency,payment_mode,payment_status,dentists(name),"
        "appointment_services(service_id,service_name,duration_minutes,start_at,end_at)"
    ).eq("clinic_id", _clinic_id()).in_("patient_id", patient_ids).order(
        "start_at"
    ).limit(50).execute().data or []


def _appointment_state(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    now = datetime.now(timezone.utc)
    upcoming, past, cancelled = [], [], []
    for row in rows:
        if row.get("status") == "cancelled":
            cancelled.append(row)
            continue
        end_at = _parse_ts(row.get("end_at"))
        if row.get("status") in {"completed", "no_show"} or (end_at is not None and end_at < now):
            past.append(row)
        else:
            upcoming.append(row)
    return upcoming, past, cancelled


async def _my_appointments(update, patients: list[dict], context):
    message = getattr(update, "effective_message", None) or update.message
    patient_ids = [row["id"] for row in patients]
    if not patient_ids:
        await message.reply_text("No appointments were found for that phone number or email.",
                                 reply_markup=_menu())
        return
    try:
        clinic = await asyncio.to_thread(get_active_clinic, _clinic_id())
        rows = await asyncio.to_thread(_load_patient_appointments, patient_ids)
    except Exception:
        logger.exception("Telegram appointment dashboard load failed")
        await message.reply_text("Appointments are temporarily unavailable.")
        return

    context.user_data["appointments"] = {row["id"]: row for row in rows}
    context.user_data["lookup_patients"] = patients
    upcoming, past, cancelled = _appointment_state(rows)

    lines = [f"👤 {_patient_profile(row)}" for row in patients]
    if not rows:
        lines += ["", "No appointments found."]
    else:
        for title, group in (
            ("Upcoming appointments", upcoming),
            ("Past appointments", past),
            ("Cancelled appointments", cancelled),
        ):
            if not group:
                continue
            lines += ["", f"{title}:"]
            for row in group:
                lines += _appointment_detail_lines(row, clinic.timezone)

    buttons = []
    for row in upcoming[:4]:
        reference = row.get("appointment_reference") or row["id"][:8]
        buttons.append([
            InlineKeyboardButton(f"View {reference}", callback_data=f"appt-view:{row['id']}"),
            InlineKeyboardButton(f"Reschedule {reference}", callback_data=f"appt-reschedule:{row['id']}"),
        ])
        buttons.append([
            InlineKeyboardButton(f"Cancel {reference}", callback_data=f"appt-cancel:{row['id']}"),
        ])
        if row.get("booking_request_id"):
            buttons.append([
                InlineKeyboardButton(
                    f"Cancel whole plan {reference}",
                    callback_data=f"plan-cancel:{row['booking_request_id']}",
                )
            ])
    for row in past[:3] + cancelled[:3]:
        reference = row.get("appointment_reference") or row["id"][:8]
        buttons.append([
            InlineKeyboardButton(f"View {reference}", callback_data=f"appt-view:{row['id']}")
        ])

    await message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else _menu(),
    )


def _cancel_linked_booking_request(client, booking_request_id: str) -> None:
    remaining = (
        client.table("appointments")
        .select("id")
        .eq("clinic_id", _clinic_id())
        .eq("booking_request_id", booking_request_id)
        .in_("status", ["pending", "confirmed"])
        .execute()
        .data
        or []
    )
    if remaining:
        return
    client.table("booking_requests").update({"status": "cancelled"}).eq(
        "clinic_id", _clinic_id()
    ).eq("id", booking_request_id).execute()


async def _cancel_stored_appointments(rows: list[dict]) -> list[tuple[dict, bool, str | None]]:
    client = _client()
    outcomes = []
    for row in rows:
        try:
            result = await asyncio.to_thread(
                cancel_appointment,
                _clinic_id(),
                row["contact_id"],
                row["id"],
                actor=AppointmentActor(
                    actor_type=AppointmentActorType.PATIENT,
                    actor_id=row["patient_id"],
                ),
            )
            ok = result.status.value in {"CANCELLED", "ALREADY_CANCELLED"}
            if ok:
                await asyncio.to_thread(
                    lambda appointment_id=row["id"]: client.table("outbound_notifications")
                    .update({"status": "cancelled"})
                    .eq("clinic_id", _clinic_id())
                    .eq("appointment_id", appointment_id)
                    .eq("status", "pending")
                    .execute()
                )
                if row.get("booking_request_id"):
                    try:
                        await asyncio.to_thread(
                            _cancel_linked_booking_request,
                            client,
                            row["booking_request_id"],
                        )
                    except Exception:
                        logger.exception(
                            "Booking request cancellation failed after appointment cancel",
                            extra={"booking_request_id": row["booking_request_id"]},
                        )
            outcomes.append((row, ok, result.reason))
        except Exception:
            logger.exception("Stored appointment cancellation failed", extra={"appointment_id": row.get("id")})
            outcomes.append((row, False, "The appointment could not be cancelled."))
    return outcomes


def _reschedule_target(row: dict) -> dict:
    services = _appointment_service_rows(row)
    duration = sum(int(line.get("duration_minutes") or 0) for line in services)
    if duration <= 0:
        start = _parse_ts(row.get("start_at"))
        end = _parse_ts(row.get("end_at"))
        if start and end:
            duration = int((end - start).total_seconds() // 60)
    service_ids = [line["service_id"] for line in services if line.get("service_id")]
    if not service_ids and row.get("service_id"):
        service_ids = [row["service_id"]]
    names = ", ".join(line.get("service_name") for line in services if line.get("service_name"))
    return {
        "clinic_id": _clinic_id(),
        "appointment_id": row["id"],
        "contact_id": row["contact_id"],
        "patient_id": row["patient_id"],
        "dentist_id": row["dentist_id"],
        "event_id": row.get("google_calendar_event_id"),
        "duration_minutes": duration or 30,
        "service_ids": service_ids,
        "service_names": names or "Appointment",
        "reference": row.get("appointment_reference") or row["id"][:8],
        "old_start_at": row.get("start_at"),
    }


async def _reschedule_find_slots(update, context):
    message = update.effective_message
    target = context.user_data.get("reschedule_target")
    if not target:
        context.user_data.pop(STATE, None)
        await message.reply_text("Please choose an appointment to reschedule.", reply_markup=_menu())
        return
    try:
        requested_date = date.fromisoformat(message.text.strip())
        clinic = await asyncio.to_thread(get_active_clinic, target["clinic_id"])
        timezone = ZoneInfo(clinic.timezone)
        day_start = datetime.combine(requested_date, time.min, tzinfo=timezone)
        result = await asyncio.to_thread(
            find_available_slots,
            target["clinic_id"],
            target["dentist_id"],
            None,
            day_start,
            day_start + timedelta(days=1),
            20,
            exclude_appointment_id=target["appointment_id"],
            exclude_calendar_event_id=target["event_id"],
            duration_override_minutes=target["duration_minutes"],
        )
    except ValueError:
        await message.reply_text("Send the date as YYYY-MM-DD.")
        return
    except Exception:
        logger.exception("Telegram reschedule availability search failed")
        await message.reply_text("I couldn't check availability right now. Please try later.")
        return
    if not result.slots:
        await message.reply_text("No available times on that date. Send another date.", reply_markup=_menu())
        return
    context.user_data["slots"] = result.slots
    rows = [
        [InlineKeyboardButton(
            slot.start_at.astimezone(timezone).strftime("%a %d %b, %I:%M %p"),
            callback_data=f"resched-slot:{index}",
        )]
        for index, slot in enumerate(result.slots)
    ]
    await message.reply_text(
        f"Choose a new time for {target['reference']}:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _shift_appointment_service_times(row: dict, delta: timedelta) -> None:
    client = _client()
    for line in _appointment_service_rows(row):
        start = _parse_ts(line.get("start_at"))
        end = _parse_ts(line.get("end_at"))
        if start is None or end is None:
            continue
        client.table("appointment_services").update(
            {
                "start_at": (start + delta).isoformat(),
                "end_at": (end + delta).isoformat(),
            }
        ).eq("clinic_id", _clinic_id()).eq("appointment_id", row["id"]).eq(
            "service_id", line["service_id"]
        ).execute()


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
        if text == "Dental Services":
            await _services(update)
        elif text == "Our Doctors":
            await _doctors(update)
        elif text == "Book Appointment":
            await _begin_booking(update, context)
        elif text == "My Appointments":
            await _begin_lookup(update, context, "appointments")
        elif text == "Payments & Pricing":
            await _payments_menu(update)
        elif text == "Help & Contact Reception":
            await _help(update)
        elif text == "Ask Marina AI":
            context.user_data[STATE] = "ai"
            await msg.reply_text("Ask Marina AI a question about the clinic.")
        return
    if text.casefold() in {"main menu", "menu", "home"}:
        _clear_flow(context)
        await msg.reply_text("Main menu", reply_markup=_menu())
        return
    if state == "booking_identifier":
        await _verify_booking_identifier(update, context)
    elif state == "booking_name":
        await _verify_booking_name(update, context)
    elif state == "booking_age":
        await _verify_booking_age(update, context)
    elif state == "booking_gender":
        await _verify_booking_gender(update, context)
    elif state == "lookup_identifier":
        await _verify_lookup_identifier(update, context)
    elif state == "date":
        await _find_slots(update, context)
    elif state == "reschedule_date":
        await _reschedule_find_slots(update, context)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Starting Marina Smile Telegram bot polling")
    build_application().run_polling()


if __name__ == "__main__":
    main()
