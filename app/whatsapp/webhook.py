from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.operations.clinic_hours import clinic_date_error, get_clinic_holidays
from app.operations.patient_booking import (
    BookingSlotTakenError, PatientBookingError, create_booking_request,
    finalize_booking_request, lookup_identities, normalize_phone,
    phone_matches, resolve_or_register_patient,
)
from app.operations.patient_channel import WELCOME_MESSAGE
from app.operations.slot_finder import find_available_slots
from app.repositories.clinics import get_active_clinic
from app.repositories.dentists import filter_dentists_for_service, list_active_dentists
from app.whatsapp.client import send_text
from app.whatsapp.privacy import WhatsAppSafeSupabase

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
logger = logging.getLogger(__name__)
MENU = "Reply *Book appointment* to book, or *Menu* to see this message again."
EMERGENCY_TERMS = (
    "difficulty breathing", "can't breathe", "cannot breathe",
    "difficulty swallowing", "can't swallow", "cannot swallow",
    "severe facial swelling", "uncontrolled bleeding", "bleeding won't stop",
)
EMERGENCY_REPLY = (
    "This may be a medical emergency. Difficulty breathing or swallowing, severe facial "
    "swelling, or uncontrolled bleeding needs immediate professional care. Please call "
    "your local emergency number or go to the nearest emergency department now."
)


def _settings():
    settings = get_settings()
    if not all((settings.whatsapp_verify_token, settings.whatsapp_app_secret,
                settings.whatsapp_access_token, settings.whatsapp_phone_number_id,
                settings.whatsapp_graph_api_version)):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    return settings


def _valid_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.removeprefix("sha256="))


@router.get("")
async def verify_webhook(request: Request) -> Response:
    settings = _settings()
    params = request.query_params
    expected = settings.whatsapp_verify_token.get_secret_value()
    if (params.get("hub.mode") == "subscribe"
            and hmac.compare_digest(params.get("hub.verify_token", ""), expected)
            and params.get("hub.challenge")):
        return Response(params["hub.challenge"], media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    settings = _settings()
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256"),
                            settings.whatsapp_app_secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in (change.get("value") or {}).get("messages", []):
                if message.get("type") == "text":
                    sender = message.get("from", "")
                    text = (message.get("text") or {}).get("body", "").strip()
                    external_id = message.get("id", "")
                    if sender.isdecimal() and 7 <= len(sender) <= 15 and text and external_id:
                        background_tasks.add_task(_process_safely, sender, external_id, text)
    return {"status": "ok"}


async def _process_safely(sender: str, message_id: str, text: str) -> None:
    try:
        await _process(sender, message_id, text)
    except Exception:
        logger.exception("WhatsApp inbound processing failed")


async def _send(sender: str, text: str) -> None:
    await send_text(sender, text)


def _db():
    return WhatsAppSafeSupabase(get_supabase_client())


def _clinic_id() -> str:
    return str(get_settings().default_clinic_id)


def _get_conversation(sender: str) -> tuple[dict, dict]:
    db, clinic = _db(), _clinic_id()
    contacts = db.table("contacts").select("id,phone_number,whatsapp_id").eq(
        "clinic_id", clinic).eq("whatsapp_id", sender).execute().data or []
    contact = contacts[0] if contacts else None
    if not contact:
        matches = [row for row in db.table("contacts").select(
            "id,phone_number,whatsapp_id").eq("clinic_id", clinic).not_.is_(
            "phone_number", "null").execute().data or []
            if phone_matches(row.get("phone_number"), sender)]
        if len(matches) > 1:
            raise RuntimeError("WhatsApp sender matches multiple patient contacts")
        contact = matches[0] if matches else None
    if not contact:
        try:
            contact = (db.table("contacts").insert({"clinic_id": clinic,
                "whatsapp_id": sender, "phone_number": normalize_phone(sender),
                "name": "WhatsApp patient"}).execute().data or [None])[0]
        except APIError as exc:
            if exc.code != "23505":
                raise
            # A concurrent Telegram/WhatsApp registration won the normalized
            # phone uniqueness race. Re-resolve by the verified phone number.
            matches = [row for row in db.table("contacts").select(
                "id,phone_number,whatsapp_id").eq("clinic_id", clinic).execute().data or []
                if phone_matches(row.get("phone_number"), sender)]
            if len(matches) != 1:
                raise RuntimeError("WhatsApp contact could not be uniquely resolved") from exc
            contact = matches[0]
    if not contact:
        raise RuntimeError("WhatsApp contact could not be resolved")
    db.table("contacts").update({"whatsapp_id": sender}).eq(
        "clinic_id", clinic).eq("id", contact["id"]).execute()
    rows = db.table("conversations").select(
        "id,contact_id,workflow_mode,workflow_step,workflow_state").eq(
        "clinic_id", clinic).eq("contact_id", contact["id"]).eq(
        "channel", "whatsapp").eq("status", "open").order(
        "updated_at", desc=True).limit(1).execute().data or []
    if rows:
        return contact, rows[0]
    row = (db.table("conversations").insert({"clinic_id": clinic,
        "contact_id": contact["id"], "channel": "whatsapp", "status": "open",
        "workflow_mode": "GENERAL_QUERY", "workflow_state": {}}).execute().data or [None])[0]
    return contact, row


def _state(conversation: dict, step: str | None, state: dict) -> None:
    _db().table("conversations").update({"workflow_mode": "BOOKING" if step else "GENERAL_QUERY",
        "workflow_step": step, "workflow_state": state if step else {},
        "last_message_at": datetime.now().astimezone().isoformat()}).eq(
        "clinic_id", _clinic_id()).eq("id", conversation["id"]).execute()


async def _reply(sender: str, contact: dict, conversation: dict, text: str) -> None:
    await _send(sender, text)
    _db().table("messages").insert({"clinic_id": _clinic_id(),
        "conversation_id": conversation["id"], "contact_id": contact["id"],
        "direction": "outbound", "role": "assistant", "text": text}).execute()


async def _process(sender: str, message_id: str, text: str) -> None:
    db = _db()
    contact, conversation = _get_conversation(sender)
    try:
        db.table("messages").insert({"clinic_id": _clinic_id(),
            "conversation_id": conversation["id"], "contact_id": contact["id"],
            "external_message_id": message_id, "direction": "inbound", "role": "user",
            "text": text}).execute()
    except APIError as exc:
        if exc.code == "23505":
            return
        raise
    normalized = text.casefold().strip()
    step, state = conversation.get("workflow_step"), conversation.get("workflow_state") or {}
    if normalized in {"menu", "home", "cancel", "stop"}:
        _state(conversation, None, {})
        await _reply(sender, contact, conversation, WELCOME_MESSAGE + "\n\n" + MENU)
    elif any(term in normalized for term in EMERGENCY_TERMS):
        await _reply(sender, contact, conversation, EMERGENCY_REPLY)
    elif not step and normalized in {"book", "booking", "book appointment", "appointment"}:
        await _begin(sender, contact, conversation)
    elif not step:
        await _reply(sender, contact, conversation, WELCOME_MESSAGE + "\n\n" + MENU)
    else:
        await _advance(sender, contact, conversation, step, state, text.strip())


async def _begin(sender: str, contact: dict, conversation: dict) -> None:
    _db().table("contacts").update({
        "preferred_notification_channel": "whatsapp",
    }).eq("clinic_id", _clinic_id()).eq("id", contact["id"]).execute()
    _db().table("outbound_notifications").update({"channel": "whatsapp"}).eq(
        "clinic_id", _clinic_id()).eq("contact_id", contact["id"]).eq(
        "status", "pending").execute()
    try:
        info = await _run(lookup_identities, _clinic_id(), "phone", normalize_phone(sender), client=_db())
    except PatientBookingError as exc:
        _state(conversation, None, {})
        await _reply(sender, contact, conversation, str(exc))
        return
    except Exception:
        logger.exception("WhatsApp patient identity lookup failed")
        await _reply(sender, contact, conversation, "I couldn't safely verify this number right now. Please try again later or contact reception.")
        return
    own_ids = {c["id"] for c in info.get("contacts", []) if c["id"] == contact["id"]}
    patients = [p for p in info.get("patients", []) if p.get("active", True)
                and p.get("primary_contact_id") in own_ids]
    state = {"phone": normalize_phone(sender), "profile": {}}
    if len(patients) == 1:
        state["patient"] = _patient(patients[0], contact)
        await _services(sender, contact, conversation, state)
    elif len(patients) > 1:
        state["patients"] = [{"id": p["id"], "name": p["full_name"]} for p in patients]
        _state(conversation, "patient", state)
        await _reply(sender, contact, conversation, "More than one patient is linked to this number. Enter the patient's full registered name.")
    else:
        _state(conversation, "name", state)
        await _reply(sender, contact, conversation, "Let's start your booking. What is the patient's full name?")


def _patient(row: dict, contact: dict) -> dict:
    return {"clinic_id": _clinic_id(), "contact_id": contact["id"],
        "patient_id": row["id"], "patient_number": row.get("patient_number"),
        "name": row.get("full_name"), "age_at_registration": row.get("age_at_registration"),
        "gender": row.get("gender")}


async def _advance(sender: str, contact: dict, conv: dict, step: str, state: dict, text: str) -> None:
    if step == "patient":
        match = [p for p in state.get("patients", []) if p["name"].casefold() == text.casefold()]
        if len(match) != 1:
            await _reply(sender, contact, conv, "That name did not match. Enter the full registered name or send Cancel.")
            return
        rows = _db().table("patients").select(
            "id,patient_number,full_name,primary_contact_id,active,age_at_registration,gender").eq(
            "clinic_id", _clinic_id()).eq("id", match[0]["id"]).eq(
            "primary_contact_id", contact["id"]).execute().data or []
        if not rows:
            raise RuntimeError("Patient is not linked to this WhatsApp contact")
        state["patient"] = _patient(rows[0], contact)
        await _services(sender, contact, conv, state)
    elif step == "name":
        if len(text) < 2 or len(text) > 120:
            await _reply(sender, contact, conv, "Please enter the patient's full name.")
            return
        state["profile"]["name"] = text
        _state(conv, "age", state)
        await _reply(sender, contact, conv, "What is the patient's age in years?")
    elif step == "age":
        if not text.isdecimal() or not 0 <= int(text) <= 120:
            await _reply(sender, contact, conv, "Enter a valid age between 0 and 120.")
            return
        state["profile"]["age"] = int(text)
        _state(conv, "gender", state)
        await _reply(sender, contact, conv, "What is the patient's gender? Reply Male, Female, or Other.")
    elif step == "gender":
        if text.casefold() not in {"male", "female", "other"}:
            await _reply(sender, contact, conv, "Reply Male, Female, or Other.")
            return
        state["profile"]["gender"] = text.casefold()
        await _services(sender, contact, conv, state)
    elif step == "services":
        indexes = _indexes(text, len(state.get("services", [])))
        if not indexes:
            await _reply(sender, contact, conv, "Reply with one or more service numbers, separated by commas.")
            return
        state["selected"] = [state["services"][i]["id"] for i in indexes]
        docs = await _run(_eligible, _clinic_id(), state["selected"], client=_db())
        if not docs:
            await _reply(sender, contact, conv, "No single doctor provides all those services. Choose fewer services or send Menu.")
            return
        state["doctors"] = [{"id": d.id, "name": d.name} for d in docs]
        _state(conv, "doctor", state)
        await _reply(sender, contact, conv, "Choose a doctor by number:\n" + _numbered([d["name"] for d in state["doctors"]]))
    elif step == "doctor":
        i = _index(text, len(state.get("doctors", [])))
        if i is None:
            await _reply(sender, contact, conv, "Reply with a doctor number from the list.")
            return
        state["doctor"] = state["doctors"][i]
        _state(conv, "date", state)
        clinic = await _run(get_active_clinic, _clinic_id(), client=_db())
        today = datetime.now(ZoneInfo(clinic.timezone)).date()
        await _reply(sender, contact, conv, f"Send your preferred date as YYYY-MM-DD, from today through {(today + timedelta(days=30)).isoformat()}. Weekends and public holidays are closed.")
    elif step == "date":
        try:
            chosen = date.fromisoformat(text)
        except ValueError:
            await _reply(sender, contact, conv, "Send the date as YYYY-MM-DD, for example 2026-11-27.")
            return
        clinic = await _run(get_active_clinic, _clinic_id(), client=_db())
        tz = ZoneInfo(clinic.timezone)
        today = datetime.now(tz).date()
        last = today + timedelta(days=30)
        holidays = set(await _run(get_clinic_holidays, _clinic_id(), today, last, client=_db()))
        error = clinic_date_error(clinic, chosen, holidays)
        if chosen < today or chosen > last or error:
            await _reply(sender, contact, conv, error or "Choose a date from today through the next 30 days.")
            return
        duration = sum(int(s.get("duration_minutes") or 0) for s in state["services"] if s["id"] in state["selected"])
        start = datetime.combine(chosen, time.min, tzinfo=tz)
        day_end = start + timedelta(days=1)
        if chosen == today:
            start = max(start, datetime.now(tz) + timedelta(hours=1))
        found = await _run(find_available_slots, _clinic_id(), state["doctor"]["id"],
            state["selected"][0], start, day_end, 10,
            duration_override_minutes=duration, client=_db())
        if not found.slots:
            await _reply(sender, contact, conv, "No live appointment times are available on that date. Send another weekday date.")
            return
        state["slots"] = [slot.model_dump(mode="json") for slot in found.slots]
        state["date"] = chosen.isoformat()
        _state(conv, "slot", state)
        labels = [datetime.fromisoformat(s["start_at"]).astimezone(tz).strftime("%I:%M %p") for s in state["slots"]]
        await _reply(sender, contact, conv, "Available times (UAE time). Choose a number:\n" + _numbered(labels))
    elif step == "slot":
        i = _index(text, len(state.get("slots", [])))
        if i is None:
            await _reply(sender, contact, conv, "Reply with an available time number.")
            return
        state["slot"] = state["slots"][i]
        services = [s for s in state["services"] if s["id"] in state["selected"]]
        total = sum(float(s.get("price_from") or 0) for s in services)
        state["total"] = total
        clinic = await _run(get_active_clinic, _clinic_id(), client=_db())
        when = datetime.fromisoformat(state["slot"]["start_at"]).astimezone(ZoneInfo(clinic.timezone)).strftime("%a %d %b %Y, %I:%M %p")
        _state(conv, "confirm", state)
        patient = state.get("patient") or state.get("profile", {})
        await _reply(sender, contact, conv, "Please review your booking:\n"
            f"Patient: {patient.get('name') or patient.get('full_name')}\nDoctor: {state['doctor']['name']}\n"
            f"Services: {', '.join(s['name'] for s in services)}\nWhen: {when} (UAE time)\n"
            f"Total: AED {total:,.2f}\nPayment: cash in hand, unpaid\n\n"
            "Reply *Confirm* to book, or *Cancel* to return to the menu.")
    elif step == "confirm":
        if text.casefold() not in {"confirm", "yes", "confirm booking"}:
            await _reply(sender, contact, conv, "Reply Confirm to book, or Cancel to return to the menu.")
            return
        await _confirm(sender, contact, conv, state)
    else:
        _state(conv, None, {})
        await _reply(sender, contact, conv, WELCOME_MESSAGE + "\n\n" + MENU)


async def _services(sender: str, contact: dict, conv: dict, state: dict) -> None:
    rows = _db().table("services").select("id,name,duration_minutes,price_from,currency").eq(
        "clinic_id", _clinic_id()).eq("active", True).order("name").execute().data or []
    if not rows:
        _state(conv, None, {})
        await _reply(sender, contact, conv, "There are no active services available to book right now.")
        return
    state["services"] = rows
    _state(conv, "services", state)
    lines = [f"{i + 1}. {s['name']} — AED {float(s.get('price_from') or 0):,.2f}" for i, s in enumerate(rows)]
    await _reply(sender, contact, conv, "Choose one or more services by number, separated by commas:\n" + "\n".join(lines))


def _eligible(clinic_id: str, service_ids: list[str], *, client=None):
    dentists = list_active_dentists(clinic_id, client=client)
    for service_id in service_ids:
        allowed = {d.id for d in filter_dentists_for_service(clinic_id, dentists, service_id, client=client)}
        dentists = [d for d in dentists if d.id in allowed]
    return dentists


async def _confirm(sender: str, contact: dict, conv: dict, state: dict) -> None:
    try:
        patient = state.get("patient")
        if not patient:
            profile = state["profile"]
            patient = await _run(resolve_or_register_patient, _clinic_id(), "phone",
                state["phone"], profile["name"], age_at_registration=profile["age"],
                gender=profile["gender"], client=_db())
        start = datetime.fromisoformat(state["slot"]["start_at"])
        cursor, service_lines = start, []
        for row in state["services"]:
            if row["id"] not in state["selected"]:
                continue
            end = cursor + timedelta(minutes=int(row["duration_minutes"]))
            service_lines.append({"service_id": row["id"], "name": row["name"],
                "price_amount": row.get("price_from") or 0,
                "duration_minutes": row["duration_minutes"], "currency": row.get("currency") or "AED",
                "dentist_id": state["doctor"]["id"], "start_at": cursor.isoformat(), "end_at": end.isoformat()})
            cursor = end
        request_row = await _run(create_booking_request, clinic_id=_clinic_id(), patient=patient,
            services=service_lines, dentist_id=state["doctor"]["id"], start_at=start,
            end_at=cursor, payment_mode="cash", client=_db())
        result = await _run(finalize_booking_request, _clinic_id(), request_row["id"],
            client=_db(), notification_channel="whatsapp")
        if result.get("status") != "confirmed":
            await _run(_cancel_booking_request, request_row["id"])
            raise PatientBookingError("The appointment could not be confirmed. Please choose another available time.")
        reference = (result.get("appointment") or {}).get("appointment_reference") or "confirmed"
        _state(conv, None, {})
        await _reply(sender, contact, conv, f"Your appointment is confirmed. Reference: {reference}.\n"
            f"Patient ID: {patient.get('patient_number') or 'registered'}\nDoctor: {state['doctor']['name']}\n"
            f"Services: {', '.join(s['name'] for s in service_lines)}\nPayment is due in cash at the clinic.")
    except BookingSlotTakenError:
        if "request_row" in locals():
            await _run(_cancel_booking_request, request_row["id"])
        _state(conv, "date", state)
        await _reply(sender, contact, conv, "That slot was just taken. Send another date and I’ll check live availability again.")
    except PatientBookingError as exc:
        await _reply(sender, contact, conv, str(exc))
    except Exception:
        logger.exception("WhatsApp booking confirmation failed")
        if "request_row" in locals():
            await _run(_cancel_booking_request, request_row["id"])
        await _reply(sender, contact, conv, "I couldn't confirm the booking. Please try again shortly.")


def _cancel_booking_request(request_id: str) -> None:
    try:
        _db().rpc("transition_booking_request", {
            "p_booking_request_id": request_id,
            "p_expected_status": "awaiting_slot_selection",
            "p_target_status": "cancelled",
        }).execute()
    except Exception:
        logger.exception("Failed to cancel unconfirmed WhatsApp booking request")


async def _run(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def _indexes(value: str, count: int) -> list[int]:
    try:
        indexes = [int(part.strip()) - 1 for part in value.split(",")]
    except ValueError:
        return []
    return indexes if indexes and len(set(indexes)) == len(indexes) and all(0 <= i < count for i in indexes) else []


def _index(value: str, count: int) -> int | None:
    indexes = _indexes(value, count)
    return indexes[0] if len(indexes) == 1 else None


def _numbered(values: list[str]) -> str:
    return "\n".join(f"{i + 1}. {value}" for i, value in enumerate(values))
