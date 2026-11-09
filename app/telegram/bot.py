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


def _patient_profile(patient: dict, *, include_id: bool = False) -> str:
    age = patient.get("age_at_registration")
    age_text = f"Age: {age}" if age is not None else "Age: not provided"
    gender = patient.get("gender")
    gender_text = f"Gender: {gender}" if gender else "Gender: not provided"
    text = f"{patient.get('full_name') or patient.get('name') or 'Patient'} · {age_text} · {gender_text}"
    patient_number = patient.get("patient_number")
    if include_id and patient_number:
        text += f" · ID: {patient_number}"
    return text


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
        "booking_patient",
        "booking_chat_id",
        "slot_profile",
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
    context.user_data["booking_chat_id"] = (
        update.effective_chat.id if update.effective_chat else None
    )
    await _continue_booking_identifier(update, context)


def _patient_from_row(row: dict) -> dict:
    return {
        "clinic_id": _clinic_id(),
        "contact_id": row["primary_contact_id"],
        "patient_id": row["id"],
        "patient_number": row.get("patient_number"),
        "name": row["full_name"],
        "age_at_registration": row.get("age_at_registration"),
        "gender": row.get("gender"),
    }


async def _continue_booking_identifier(update, context):
    identifier = context.user_data.get("booking_identifier")
    if not identifier:
        _clear_flow(context)
        await update.effective_message.reply_text(
            "Please start again with Book Appointment.", reply_markup=_menu()
        )
        return
    identifier_type, identifier_value = identifier
    try:
        info = await asyncio.to_thread(
            lookup_identities, _clinic_id(), identifier_type, identifier_value
        )
    except Exception:
        logger.exception("Telegram patient lookup failed")
        await update.effective_message.reply_text(
            "I couldn't verify your record right now. Please try later."
        )
        return

    patients = [row for row in info.get("patients", []) if row.get("active", True)]
    if len(patients) > 1:
        context.user_data["booking_patients"] = patients
        context.user_data[STATE] = "booking_name"
        await update.effective_message.reply_text(
            "More than one patient uses this phone/email. Enter the full name of the patient booking."
        )
        return
    patient = _patient_from_row(patients[0]) if patients else None
    await _start_service_selection(update, context, patient)


async def _verify_booking_identifier(update, context):
    identifier = _normalize_identifier(update.effective_message.text or "")
    if identifier is None:
        await update.effective_message.reply_text(IDENTIFIER_PROMPT)
        return
    context.user_data["booking_identifier"] = identifier
    context.user_data["booking_chat_id"] = (
        update.effective_chat.id if update.effective_chat else None
    )
    await _continue_booking_identifier(update, context)


async def _verify_booking_name(update, context):
    name = " ".join((update.effective_message.text or "").split())
    if len(name) < 2:
        await update.effective_message.reply_text("Enter your full name.")
        return
    patients = context.user_data.get("booking_patients") or []
    matches = [
        row for row in patients
        if normalize_name(row["full_name"]) == normalize_name(name)
    ]
    if len(matches) != 1:
        await update.effective_message.reply_text(
            "That name does not match a patient for this phone/email. Enter the full registered name."
        )
        return
    context.user_data.pop("booking_patients", None)
    patient = _patient_from_row(matches[0])
    await _start_service_selection(update, context, patient)


async def _start_service_selection(update, context, patient, reply_message=None):
    message = (
        reply_message
        or getattr(update, "effective_message", None)
        or getattr(update, "message", None)
    )
    context.user_data["booking_patient"] = patient
    if patient:
        try:
            existing = await asyncio.to_thread(
                active_booking_for_patient, patient["clinic_id"], patient["patient_id"]
            )
        except Exception:
            logger.exception("Telegram duplicate booking check failed")
            await message.reply_text("I couldn't verify your bookings. Please try again.")
            return
        if existing:
            _clear_flow(context)
            await message.reply_text(describe_active_booking(existing), reply_markup=_menu())
            return
    try:
        services = await asyncio.to_thread(_load_service_rows, _clinic_id())
    except Exception:
        logger.exception("Telegram booking services lookup failed")
        await message.reply_text("Booking is temporarily unavailable.")
        return
    if not services:
        await message.reply_text(
            "No active services are available to book.", reply_markup=_menu()
        )
        return
    context.user_data["booking"] = {"services": services, "selected": []}
    context.user_data[STATE] = "booking"
    await message.reply_text("Booking started.", reply_markup=_menu())
    await message.reply_text(
        "Choose one or more services. Your bill is calculated automatically:",
        reply_markup=_service_keyboard(context.user_data["booking"]),
    )


def _gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Male", callback_data="sgender:male"),
            InlineKeyboardButton("Female", callback_data="sgender:female"),
            InlineKeyboardButton("Other", callback_data="sgender:other"),
        ],
        _main_menu_row(),
    ])


def _next_profile_step(patient: dict | None, profile: dict) -> str | None:
    if patient is None and not profile.get("name"):
        return "name"
    if (
        patient is None or patient.get("age_at_registration") is None
    ) and profile.get("age") is None:
        return "age"
    if (patient is None or not patient.get("gender")) and not profile.get("gender"):
        return "gender"
    return None


async def _ask_profile(message, context):
    patient = context.user_data.get("booking_patient")
    profile = context.user_data.setdefault(
        "slot_profile", {"name": None, "age": None, "gender": None}
    )
    step = _next_profile_step(patient, profile)
    if step == "name":
        context.user_data[STATE] = "slot_profile_name"
        await message.reply_text("Enter the patient's full name.")
    elif step == "age":
        context.user_data[STATE] = "slot_profile_age"
        await message.reply_text(
            "Enter the patient's age in years (for example: 30)."
        )
    elif step == "gender":
        context.user_data[STATE] = "slot_profile_gender"
        await message.reply_text(
            "Select the patient's gender:", reply_markup=_gender_keyboard()
        )
    else:
        context.user_data.pop(STATE, None)
        await _confirm_selected_booking(message, context)


async def _verify_slot_profile_name(update, context):
    name = " ".join((update.effective_message.text or "").split())
    if len(name) < 2:
        await update.effective_message.reply_text("Enter the patient's full name.")
        return
    context.user_data.setdefault("slot_profile", {})["name"] = name
    await _ask_profile(update.effective_message, context)


async def _verify_slot_profile_age(update, context):
    try:
        age = int((update.effective_message.text or "").strip())
    except ValueError:
        await update.effective_message.reply_text(
            "Enter age as a whole number, for example: 30."
        )
        return
    if not 0 <= age <= 120:
        await update.effective_message.reply_text("Enter a valid age between 0 and 120.")
        return
    context.user_data.setdefault("slot_profile", {})["age"] = age
    await _ask_profile(update.effective_message, context)


async def _verify_slot_profile_gender(update, context):
    value = (update.effective_message.text or "").strip().casefold()
    if value not in {"male", "female", "other"}:
        await update.effective_message.reply_text(
            "Enter gender as male, female, or other.", reply_markup=_gender_keyboard()
        )
        return
    await _set_slot_profile_gender(update.effective_message, context, value)


async def _set_slot_profile_gender(message, context, gender: str):
    context.user_data.setdefault("slot_profile", {})["gender"] = gender
    await _ask_profile(message, context)


def _persist_booking_patient(context) -> dict:
    patient = context.user_data.get("booking_patient")
    profile = context.user_data.get("slot_profile") or {}
    if patient:
        updates: dict = {}
        if patient.get("age_at_registration") is None and profile.get("age") is not None:
            updates["age_at_registration"] = profile["age"]
        if not patient.get("gender") and profile.get("gender"):
            updates["gender"] = profile["gender"]
        if updates:
            _client().table("patients").update(updates).eq(
                "clinic_id", _clinic_id()
            ).eq("id", patient["patient_id"]).execute()
            patient.update(updates)
        return patient
    identifier = context.user_data.get("booking_identifier")
    name = profile.get("name")
    if not identifier or not name:
        raise PatientBookingError("Your booking details expired. Please start again.")
    return resolve_or_register_patient(
        _clinic_id(),
        identifier[0],
        identifier[1],
        name,
        age_at_registration=profile.get("age"),
        gender=profile.get("gender"),
        telegram_chat_id=context.user_data.get("booking_chat_id"),
    )


def _booking_services_with_times(booking: dict) -> list[dict]:
    cursor = _parse_ts((booking.get("slot") or {}).get("start_at"))
    if cursor is None:
        return []
    rows = []
    for service_id in booking.get("service_order") or []:
        service = (booking.get("services") or {}).get(service_id) or {}
        duration = int(service.get("duration_minutes") or 0)
        end = cursor + timedelta(minutes=duration)
        rows.append({
            "service_id": service_id,
            "name": service.get("name"),
            "price_amount": float(service.get("price_from") or 0),
            "duration_minutes": duration,
            "currency": service.get("currency") or "AED",
            "dentist_id": booking.get("dentist_id"),
            "start_at": cursor.isoformat(),
            "end_at": end.isoformat(),
        })
        cursor = end
    return rows


async def _confirm_selected_booking(message, context):
    booking = context.user_data.get("booking") or {}
    slot = booking.get("slot") or {}
    start_at = _parse_ts(slot.get("start_at"))
    end_at = _parse_ts(slot.get("end_at"))
    if (
        not booking.get("selected")
        or not booking.get("dentist_id")
        or start_at is None
        or end_at is None
    ):
        _clear_flow(context)
        await message.reply_text(
            "Your booking details expired. Please start again.", reply_markup=_menu()
        )
        return
    try:
        patient = await asyncio.to_thread(_persist_booking_patient, context)
    except PatientBookingError as exc:
        _clear_flow(context)
        await message.reply_text(str(exc), reply_markup=_menu())
        return
    except Exception:
        logger.exception("Telegram patient registration failed")
        await message.reply_text(
            "I couldn't save your patient record. Please try later."
        )
        return
    try:
        existing = await asyncio.to_thread(
            active_booking_for_patient, patient["clinic_id"], patient["patient_id"]
        )
    except Exception:
        logger.exception("Telegram duplicate booking check failed")
        existing = None
    if existing:
        _clear_flow(context)
        await message.reply_text(describe_active_booking(existing), reply_markup=_menu())
        return
    services = _booking_services_with_times(booking)
    if not services:
        _clear_flow(context)
        await message.reply_text(
            "The selected services expired. Please start again.", reply_markup=_menu()
        )
        return
    try:
        request = await asyncio.to_thread(
            create_booking_request,
            clinic_id=_clinic_id(),
            patient=patient,
            services=services,
            dentist_id=booking["dentist_id"],
            start_at=start_at,
            end_at=end_at,
            payment_mode="cash",
        )
    except PatientBookingError as exc:
        await message.reply_text(str(exc), reply_markup=_menu())
        return
    except Exception:
        logger.exception("Telegram booking request insert failed")
        await message.reply_text("I couldn't save the booking. Please try again.")
        return
    try:
        result = await asyncio.to_thread(
            finalize_booking_request, _clinic_id(), request["id"]
        )
    except Exception:
        logger.exception(
            "Telegram cash booking confirmation failed",
            extra={"request_id": request["id"]},
        )
        result = {}
    text = await _booking_confirmation_text(patient, result, request, booking, services)
    await message.reply_text(text, reply_markup=_menu())
    context.user_data.pop("booking", None)
    context.user_data.pop("slot_profile", None)
    context.user_data.pop("slots", None)
    context.user_data.pop(STATE, None)


async def _booking_confirmation_text(patient, result, request, booking, services) -> str:
    clinic = await asyncio.to_thread(get_active_clinic, _clinic_id())
    if result.get("status") == "confirmed":
        appointment = result.get("appointment") or {}
        doctor = (
            result.get("dentist_name")
            or (booking.get("dentists") or {}).get(booking.get("dentist_id"))
            or "—"
        )
        reference = appointment.get("appointment_reference") or "—"
        status = appointment.get("status") or "confirmed"
        when = _when_text(
            appointment.get("start_at") or request.get("preferred_window_start"),
            clinic.timezone,
        )
        total = (
            appointment.get("total_amount")
            if appointment.get("total_amount") is not None
            else request.get("total_amount")
        )
    else:
        doctor = (booking.get("dentists") or {}).get(booking.get("dentist_id")) or "—"
        reference = "pending confirmation"
        status = result.get("status") or "saved"
        when = _when_text(request.get("preferred_window_start"), clinic.timezone)
        total = request.get("total_amount")
    service_lines = "\n".join(
        f"   • {line['name']} · {_when_text(line.get('start_at'), clinic.timezone)}"
        for line in services
    )
    lines = [
        "✅ Appointment confirmed" if result.get("status") == "confirmed" else "Booking saved",
        "",
        f"Reference: {reference}",
        f"Patient: {patient.get('name') or '—'}",
        f"Patient ID: {patient.get('patient_number') or '—'}",
        f"Age: {patient.get('age_at_registration') if patient.get('age_at_registration') is not None else '—'}",
        f"Gender: {patient.get('gender') or '—'}",
        f"Doctor: {doctor}",
        f"Date & time: {when}",
        "Services:",
        service_lines,
        f"Total bill: {_money(total, request.get('currency') or 'AED')}",
        "Payment: cash in hand · unpaid",
        f"Status: {status}",
        "",
        "Please pay at the clinic. Use My Appointments to review, reschedule, or cancel.",
    ]
    if result.get("status") != "confirmed" and result.get("reason"):
        lines.append(f"Note: {result['reason']}")
    return "\n".join(lines)


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


def _eligible_dentists(clinic_id: str, service_ids: list[str]):
    dentists = list_active_dentists(clinic_id)
    eligible = dentists
    for service_id in service_ids:
        allowed = {
            dentist.id
            for dentist in filter_dentists_for_service(clinic_id, dentists, service_id)
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
        await _show_reschedule_slots(query, context)
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

    if data.startswith("sgender:"):
        gender = data.removeprefix("sgender:")
        if gender not in {"male", "female", "other"}:
            await query.edit_message_text("Select male, female, or other.")
            return
        await query.edit_message_text(f"Gender: {gender}.")
        await _set_slot_profile_gender(query.message, context, gender)
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
            dentists = await asyncio.to_thread(_eligible_dentists, _clinic_id(), list(booking["selected"]))
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
        booking["service_order"] = list(booking.get("selected") or [])
        context.user_data.pop(STATE, None)
        await _show_booking_slots(query, context)
        return

    if data.startswith("bslot:"):
        booking = context.user_data.get("booking") or {}
        try:
            slot = context.user_data["slots"][int(data.removeprefix("bslot:"))]
        except (KeyError, TypeError, ValueError, IndexError):
            await query.edit_message_text(
                "That time choice expired. Please choose a doctor again."
            )
            return
        booking["slot"] = {
            "start_at": slot.start_at.isoformat(),
            "end_at": slot.end_at.isoformat(),
        }
        context.user_data.pop("slots", None)
        context.user_data["slot_profile"] = {
            "name": None,
            "age": None,
            "gender": None,
        }
        context.user_data.pop(STATE, None)
        await _ask_profile(query.message, context)
        return

async def _show_booking_slots(update_or_query, context):
    booking = context.user_data.get("booking") or {}
    services, _total, duration = _selected_summary(booking)
    message = getattr(update_or_query, "message", None)
    if message is None:
        message = getattr(update_or_query, "effective_message", None)
    if not services or message is None:
        if message is not None:
            await message.edit_text(
                "Your service selection expired. Please start again."
            )
        return
    doctor_id = booking.get("dentist_id")
    doctor_name = (booking.get("dentists") or {}).get(doctor_id) or "the doctor"
    try:
        clinic = await asyncio.to_thread(get_active_clinic, _clinic_id())
        timezone = ZoneInfo(clinic.timezone)
        now = datetime.now(timezone)
        result = await asyncio.to_thread(
            find_available_slots,
            _clinic_id(),
            doctor_id,
            None,
            now,
            now + timedelta(days=get_settings().availability_search_days),
            20,
            duration_override_minutes=duration,
        )
    except Exception:
        logger.exception("Telegram booking slot search failed")
        await message.edit_text(
            "I couldn't check availability right now. Please try again."
        )
        return
    if not result.slots:
        await message.edit_text(
            f"No available times with {doctor_name} in the next "
            f"{get_settings().availability_search_days} days. Please contact reception."
        )
        return
    context.user_data["slots"] = result.slots
    rows = [
        [InlineKeyboardButton(
            slot.start_at.astimezone(timezone).strftime("%a %d %b, %I:%M %p"),
            callback_data=f"bslot:{index}",
        )]
        for index, slot in enumerate(result.slots)
    ]
    rows.append(_main_menu_row())
    await message.edit_text(
        f"Available times with {doctor_name} (live availability):",
        reply_markup=InlineKeyboardMarkup(rows),
    )


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


async def _show_reschedule_slots(update_or_query, context):
    target = context.user_data.get("reschedule_target")
    message = getattr(update_or_query, "message", None)
    if message is None:
        message = getattr(update_or_query, "effective_message", None)
    if not target or message is None:
        if message is not None:
            await message.edit_text(
                "Please choose an appointment to reschedule."
            )
        return
    try:
        clinic = await asyncio.to_thread(get_active_clinic, target["clinic_id"])
        timezone = ZoneInfo(clinic.timezone)
        now = datetime.now(timezone)
        result = await asyncio.to_thread(
            find_available_slots,
            target["clinic_id"],
            target["dentist_id"],
            None,
            now,
            now + timedelta(days=get_settings().availability_search_days),
            20,
            exclude_appointment_id=target["appointment_id"],
            exclude_calendar_event_id=target["event_id"],
            duration_override_minutes=target["duration_minutes"],
        )
    except Exception:
        logger.exception("Telegram reschedule availability search failed")
        await message.edit_text(
            "I couldn't check availability right now. Please try again."
        )
        return
    if not result.slots:
        await message.edit_text(
            f"No available times for {target['reference']} in the next "
            f"{get_settings().availability_search_days} days. Please contact reception."
        )
        return
    context.user_data["slots"] = result.slots
    rows = [
        [InlineKeyboardButton(
            slot.start_at.astimezone(timezone).strftime("%a %d %b, %I:%M %p"),
            callback_data=f"resched-slot:{index}",
        )]
        for index, slot in enumerate(result.slots)
    ]
    await message.edit_text(
        f"Available new times for {target['reference']} (live availability):",
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
    elif state == "slot_profile_name":
        await _verify_slot_profile_name(update, context)
    elif state == "slot_profile_age":
        await _verify_slot_profile_age(update, context)
    elif state == "slot_profile_gender":
        await _verify_slot_profile_gender(update, context)
    elif state == "lookup_identifier":
        await _verify_lookup_identifier(update, context)
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
