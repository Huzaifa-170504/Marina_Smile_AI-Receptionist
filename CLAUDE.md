# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Marina Smile is a dental clinic receptionist and operations platform: a Telegram patient bot, a FastAPI backend, a Next.js staff/recruiter dashboard, Supabase (PostgreSQL + Auth + Storage + pgvector), Gemini, and Google Calendar.

The core design principle is separation of trust:
- **Gemini** only interprets language and writes grounded answers.
- **FastAPI** validates, authorizes, and orchestrates.
- **PostgreSQL** is authoritative for tenant isolation, lifecycle rules, idempotency, concurrency, and audit.
- **Google Calendar** is authoritative for external availability and confirmed events.
- **Next.js** renders the public demo and the authenticated staff UI.

## Commands

The local development environment uses `venv/` on Windows (not tracked by Git).

```bash
# Backend
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
./venv/Scripts/python.exe -m pytest -q
./venv/Scripts/python.exe -m pytest tests/test_booking.py -q
./venv/Scripts/python.exe -m pytest tests/test_booking.py::test_name -q
./venv/Scripts/python.exe -m py_compile app/telegram/bot.py app/operations/patient_booking.py

# Dashboard (from dashboard/)
npm run dev
npm test
npm run lint
npm run typecheck
npm run build
npx vitest run src/__tests__/navigation.test.ts
```

Health check: `GET http://127.0.0.1:8000/health`.

Operational CLIs (they print a preview and require confirmation for mutations):

```bash
./venv/Scripts/python.exe scripts/ingest_pdf.py --clinic-id <clinic-uuid> --pdf <clinic.pdf>
./venv/Scripts/python.exe scripts/ask_knowledge.py
./venv/Scripts/python.exe scripts/check_availability.py
./venv/Scripts/python.exe scripts/book_appointment.py --help
./venv/Scripts/python.exe scripts/reschedule_appointment.py --help
./venv/Scripts/python.exe scripts/cancel_appointment.py --help
./venv/Scripts/python.exe scripts/sync_uae_holidays.py
```

`TELEGRAM_BOT_TOKEN` is optional. If set, `app.main` starts Telegram polling **and** the reminder worker inside the FastAPI lifespan. If unset, the API runs without either.

## Database migrations

There is no Supabase CLI configuration in the repo. Migrations are applied **manually in the Supabase SQL editor**, in filename order. Each file wraps its work in `begin; ... commit;`.

- Files `0001`–`0016` are the canonical base.
- Timestamped files (`20261020…`, `20261106…`, `20261109…`, `20261117…`, `20261118…`) are additive releases. Recent additions cover per-service schedules, live patient-identity reconciliation, booking-plan cancellation, the legacy payment-gate removal, privacy cleanup (including anonymized `whatsapp_id` tombstones), notification scheduling, reminder timing, multi-contact Telegram linking, the booking duplicate guard, clinic hours/holidays, Google holiday sync state, the change-request snapshot columns, owner-only holiday-sync diagnostics, and the Telegram notification-channel default. Never edit a migration that has already been applied; add a new timestamped file.
- The live project is authoritative for constraints. It requires `appointments.patient_id` and `appointments.booking_request_id` (both NOT NULL) and leaves `appointments.idempotency_key` nullable. Do not "correct" these to match the base files by weakening the live schema.
- The live database may differ from the canonical files. Previous work hit live-only constraints (`patients_check` requiring DOB/age, `contacts.phone_number NOT NULL`). When a write fails with `23514` or `23502`, inspect the live constraint and reconcile it with a new migration rather than editing applied ones.
- `supabase/tests/README.md` describes the SQL integrity/RLS suite; it requires a disposable project and is not run by `pytest`.
- `supabase/preflight/audit_live_catalog.sql` is the read-only SQL-editor audit for constraints, indexes, policy bodies, grants, triggers, data integrity, and holiday-sync health. PostgREST exposes none of these; use this script instead of assuming the live catalog matches migrations.

## Architecture

### Backend (`app/`)

- `app/main.py` — FastAPI app, CORS, lifespan-managed Telegram polling and notification worker.
- `app/api/routes/` — dashboard/API mutations. Critical writes go through PostgreSQL RPCs (`app/api/rpc.py`) with the caller's bearer token.
- `app/repositories/` — tenant-scoped reads.
- `app/operations/` — domain services. Two booking stacks coexist:
  - **Typed pipeline**: `booking.py`, `reschedule.py`, `cancellation.py`, `availability.py`, `slot_finder.py`. Used by scripts and the operational tools. It validates schedules, DB conflicts, and Calendar, then creates `pending -> confirmed` appointments. **It is not live-compatible for new bookings:** it does not supply `patient_id` or a real `booking_requests` parent, so the live schema rejects its inserts. Use the Telegram request pipeline or the reservation RPCs for real bookings; do not weaken the live constraints to make the old path fit.
  - **Telegram request pipeline**: `patient_booking.py` + `app/telegram/bot.py`. Creates `booking_requests`, then `reserve_appointment` / `confirm_appointment_reservation` RPCs. This is the live patient flow and supports multi-service bookings with per-service doctor/time rows in `booking_request_services` and `appointment_services`.
- `app/telegram/bot.py` — the patient state machine. Persistent menu: Book Appointment, My Appointments, Our Doctors, Dental Services, Ask Marina AI, Payments & Pricing, Help & Contact Reception.
  - `Book Appointment`: phone/email -> multi-service selection -> doctor eligible for all services -> date selection (today through 30 days, weekdays only, buttons or manual `YYYY-MM-DD`) -> live slots for that date/doctor/duration (today starts at now + 1 hour, clinic hours 10:00–18:00, break 13:00–14:00, weekends/holidays excluded) -> slot selection -> name/age/gender for new or incomplete patients -> review -> explicit cash-only confirmation with reference, patient ID, age, gender, doctor, services, and time.
  - `My Appointments`, payment history, and payment status: ask only for the booking phone/email, then show the patient's name, age, gender and all grouped upcoming/past/cancelled appointments. Reschedule lists live slots immediately.
  - Appointment actions: view details, reschedule (wired to `reschedule_appointment` with multi-service duration/service overrides), cancel one appointment, or cancel all active appointments. Cancellation updates the database before replying and offers a refreshed list.
  - `Our Doctors`, `Dental Services`, `Ask Marina AI`, `Payments & Pricing` (prices, methods, history, payment status, refund guidance), `Help & Contact Reception`.
  - Opening-hours questions are answered from the `clinics` row (hours, break, closed weekdays, today/tomorrow holidays) by `app/services/clinic_directory.py` before RAG. The bundled demo PDF lists different hours; it must never answer operational hours.
  - `/cancel` or typing `cancel` clears temporary workflow state and returns to the main menu without touching confirmed bookings.
  - `Main menu` is intentionally absent from the persistent keyboard; a `🏠 Main menu` inline button exists only inside the booking flow.
  - Patient lookups are bound to the Telegram chat ID stored on the contact; a different Telegram account is refused.
  - Cancelling a booking erases the patient/contact identifiers unless another active booking needs them, and removes receipt files. Non-identifying appointment/payment/audit rows remain because the audit log is append-only.
  - All bot table/RPC/Storage access goes through `PatientSafeSupabase` in `app/telegram/privacy.py`; staff, payroll, refund, and audit tables are blocked even though the bot uses the service-role key. The allowlist includes `reserve_appointment` and `confirm_appointment_reservation`, and Storage is limited to `payment-proofs`. The bot passes that client into lookup, registration, booking creation, and finalization.
- `app/telegram/notifications.py` — claims `outbound_notifications` and delivers six Telegram notification types: immediate booking/reschedule/cancellation confirmations, 24-hour and 2-hour reminders, and a 2-hour post-completion feedback request. It also calls `mark_due_appointments_served`. Appointment database triggers are authoritative for reminder timing: more than 24 hours away schedules both the 24h and 2h reminders; within 24 hours schedules only the 2h reminder; within 2 hours sends the 2h reminder as soon as possible. Rescheduling moves pending reminders to the new start, cancellation/no-show/completion cancels future reminders, and completion schedules feedback. Reminder text reports the actual time until the slot. Booking slot displays exclude past/booked times and prioritise slots freed by future cancellations.
- `app/ai/`, `app/rag/` — Gemini generation + pgvector retrieval. Operational facts must come from repositories, not model memory.
- `app/integrations/google_calendar.py` — service-account Calendar client. Availability and booking **require** `clinics.google_calendar_id`; the calendar must be shared with the service account with `Make changes to events`. Empty calendar ID raises `CalendarUnavailableError`/`CalendarWriteError`.

### Data model and invariants

Main tables: `clinics`, `staff_users`, `dentists`, `services`, `dentist_services`, `dentist_schedules`, `contacts`, `patients`, `booking_requests`, `booking_request_services`, `payments`, `appointments`, `appointment_services`, `appointment_visits`, `outbound_notifications`, `refund_requests`, `appointment_audit_log`.

Important invariants:
- Everything is clinic-scoped; composite foreign keys enforce tenancy.
- `appointments` has an exclusion constraint preventing overlapping `pending`/`confirmed` appointments per dentist.
- An appointment starts `pending`; Calendar creation precedes confirmation. Confirmed appointments require a `google_calendar_event_id`.
- Payment modes:
  - `cash`: appointment confirms immediately with `payment_status = 'unpaid'`; staff settle it later (`mark_cash_payment_paid`).
  - `online`: receipt is uploaded to `payment-proofs`; `verify_payment` marks the request `payment_verified`, and the API then finalizes the appointment.
- Contact identity is unique per clinic for normalized phone and email. One Telegram chat may be linked to more than one contact (for example, family members), but the bot still refuses a second distinct patient under the same phone/email. Patient lookups only expose contacts linked to the current Telegram chat.
- A patient may hold multiple active bookings for different services or doctors. `reserve_appointment` uses advisory locks plus a database check to reject a second active appointment for the **same service and doctor**; cancelled appointments release the service. The existing exclusion constraint prevents overlapping active appointments for the same dentist. Overdue never-checked-in visits are reconciled to `no_show`.
- Clinic hours, break, closed weekdays, and public holidays are authoritative in the database. `clinics` stores operating hours (`open_time`, `close_time`, `break_start`, `break_end`, `closed_weekdays`); `clinic_holidays` stores dated closures. Python availability/services filter with `app/operations/clinic_hours.py`, and `appointments_validate_clinic_hours` rejects invalid inserts/updates for every interface.
- Google Calendar is the primary holiday source. `app/operations/holiday_sync.py` discovers the UAE holiday calendar (calendarList first, then `UAE_HOLIDAY_CALENDAR_ID`), reads all-day events with `events.list`, and upserts `clinic_holidays` rows with `source='google_calendar'`, Google event IDs, calendar ID, and event timestamps. Incremental sync uses Google sync tokens; expired tokens fall back to a full sync. Deletions are applied only from successful responses; an empty full response or API outage never deletes existing holidays.
- Sync state and a lease live in `clinic_holiday_sync`; `claim_holiday_sync` prevents concurrent schedulers. `app/main.py` starts `run_holiday_sync_scheduler()` on startup and repeats every `HOLIDAY_SYNC_INTERVAL_HOURS` (default 6). `HOLIDAY_SYNC_ENABLED=false` disables it. Manual runs use `scripts/sync_uae_holidays.py`.
- The existing Google integration is a service account, not OAuth user tokens. `calendarList` is empty, but the public UAE holiday calendar is readable. If access fails, check calendar sharing/service-account credentials and `UAE_HOLIDAY_CALENDAR_ID`.
- Appointment confirmation, rescheduling, cancellation, 24-hour/2-hour reminders, and post-completion feedback all use `outbound_notifications`. Database triggers own reminder scheduling and cancellation; the polling worker delivers due rows and re-checks appointment status before sending.

### Dashboard (`dashboard/`)

- Next.js 16 + React 19 + TypeScript + Supabase SSR.
- Auth: Supabase Auth session -> active `staff_users` row -> role/permissions. No public registration.
- RLS governs direct reads. High-impact mutations go through the FastAPI API with the bearer token.
- Roles: `owner`, `doctor`, `receptionist`, `payment_verifier`.
- Payments page: online proof approval/rejection and cash "Mark paid".
- Most appointment/booking controls are intentionally read-only; do not assume the UI exposes every backend operation.
- `dashboard/AGENTS.md` is generated by `next dev` and warns that this Next.js version may differ from model training data. Read `dashboard/node_modules/next/dist/docs/` before writing Next.js code.

## Timezone standard

All scheduling is interpreted in **Asia/Dubai**, regardless of server or user timezone.

- `clinics.timezone` is `Asia/Dubai`; appointment/slot/date calculations use `ZoneInfo(clinic.timezone)`.
- Date selection, slot generation, rescheduling, cancellation, My Appointments, reminders, and Google appointment events use the clinic timezone.
- Holiday sync uses `UAE_TIMEZONE = "Asia/Dubai"`; all-day holiday events keep their date fields without UTC conversion.
- Timestamps are stored as `timestamptz`; date-only values remain UAE calendar dates.
- Never introduce naive `datetime.now()` or server-local date calculations in booking paths.

## Environment

Backend `.env` (see `.env.example`): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `GEMINI_API_KEY`, `DEFAULT_CLINIC_ID`, `TELEGRAM_BOT_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS`, `DASHBOARD_ALLOWED_ORIGINS`, `DASHBOARD_APPLICATION_URL`, holiday-sync settings, demo settings. A local `.env` may also contain legacy `WHATSAPP_*` entries: no WhatsApp integration code exists in this repository, so they are unused secrets.

Dashboard `.env.local` (see `dashboard/.env.example`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_BASE_URL`, plus server-only demo proxy variables.

Never expose `SUPABASE_SECRET_KEY`, Gemini keys, Google private keys, or `DEMO_API_TOKEN` through `NEXT_PUBLIC_*`. Backend scripts and the bot use the secret key and therefore bypass RLS; browser code must not.

## Verification expectations

- Python suite: 280 tests.
- Dashboard suite: 27 tests across 10 files.
- TypeScript must pass `npm run typecheck`.
- Tests do not prove live Supabase constraints, Calendar permissions, migrations, Telegram polling, or Render/Vercel deploys. Verify those separately against the hosted services.

## Guardrails and enforcement

Do not weaken these when changing workflows:

- **AI:** `app/ai/prompts.py` limits answers to Marina Smile, forbids inventing operational facts, diagnoses, prescriptions, dosages, outcome guarantees, credential/system-prompt disclosure, prompt injection, and false booking/payment claims. Gemini has no tool or SQL/shell execution.
- **Medical:** `_is_emergency()` in `app/telegram/bot.py` intercepts difficulty breathing, difficulty swallowing, severe facial swelling, and uncontrolled bleeding before AI or booking and advises immediate emergency care.
- **Identity/privacy:** patient lookups require the Telegram chat linked to the contact. `PatientSafeSupabase` blocks staff, payroll, refund, and audit tables and restricts Storage to `payment-proofs`. Staff management cannot exceed the caller's authority: a delegate can grant only permissions it holds, and only an owner can act on an owner account. Phone identifiers are canonicalized (`normalize_phone`) so UAE local/international (`04 XXX XXXX` / `+971 4 XXX XXXX`) and Pakistan (`0300 1234567` / `+92 300 1234567`) forms match the same contact. Cancellation anonymises identifiers and deletes receipt images. Do not log patient names, phones, or emails.
- **Appointments:** booking dates are limited to today through 30 days ahead, weekdays only, and today's slots start at least one hour from now. Clinic hours are 10:00–18:00 with a 13:00–14:00 break; weekends and `clinic_holidays` dates are closed. These rules are enforced by the shared `clinic_hours` module and the `appointments_validate_clinic_hours` database trigger. Booking shows a full summary and requires an explicit **Confirm booking** callback; rescheduling shows a summary and requires **Confirm reschedule**. Database exclusion constraints, the advisory-locked duplicate service/doctor guard in `reserve_appointment`, and Calendar checks are authoritative. Never auto-cancel for a missed reminder.
- **Universal navigation:** workflow replies get one inline **Cancel & Return to Main Menu** button through `install_universal_cancel()` and `app/telegram/ui.py`; the main menu itself is skipped. Local duplicate Cancel buttons were removed. Existing inline actions are preserved. `/cancel` and typed `cancel` also clear temporary state without touching confirmed bookings.
- **Holidays:** Google Calendar is the primary holiday source. Synchronization never deletes rows on an empty response or API failure, uses a lease to prevent concurrent runs, stores event IDs/sync tokens, and falls back to a full sync on an expired token. Booking reads Supabase only, never the live holiday API. `clinic_holiday_sync.last_status`/`last_error` are the operational health signal; `Google Calendar credentials are unavailable` means the deployed service-account secret file is missing or unreadable, not that holiday data was lost.
- **Payments:** screenshots stay `submitted` until staff verification. Cash settlement, approval, rejection, and refunds are staff-only RPCs. AI never modifies payments.
- **Notifications:** database triggers own reminder scheduling, moving, and cancellation. The worker re-checks appointment status before sending, uses unique event keys, and retries delivery up to 3 times with a 5-minute delay. Delivery failure never changes an appointment. Reminder previews omit treatment names; full details stay in My Appointments. Messages are transactional, not marketing.
- **Security/reliability:** per-chat rate limiting for messages and callbacks; Google Calendar timeouts; booking compensation; FastAPI/Pydantic input validation; HTTPS in hosted environments; structured logs for failed bookings, duplicate attempts, rejected lookups, notification failures, and AI fallbacks. The bot uses polling, not a webhook, so there is no webhook endpoint to validate.
- **Human handoff:** `Help & Contact Reception` can create a staff handoff via `app/operations/patient_handoff.py`.

Relevant regression tests: `tests/test_telegram_guardrails.py`, `tests/test_notifications.py`, `tests/test_telegram_appointments.py`, `tests/test_booking_concurrency.py`, `tests/test_cancellation.py`, `tests/test_reschedule.py`.

## Known gaps (verified, not implemented)

- The typed direct-booking pipeline (`scripts/book_appointment.py`) cannot write to the live schema; it needs an adapter that creates the patient and booking request first.
- Service-line child tables (`appointment_services`, `booking_request_services`) still use clinic-wide SELECT rather than inheriting the parent appointment's role scoping.
- `/api/demo/status` is token-gated but has no per-visitor quota (its sibling `/query` does).
- The bundled knowledge PDF is demo content (hours, doctors, prices, "WhatsApp" channel). Hours questions are now answered from the database, but other static facts in the PDF remain demo-only and can differ from live rows.
- No CI/CD: backend/dashboard tests, typecheck, and lint run only on a developer machine.
- Multi-doctor/multi-visit booking plans, alternative-combination ranking, and partial-booking consent.
- Temporary slot holds and waiting-list management.
- Per-service cancellation inside a multi-service appointment (the current plan is one appointment row; cancellation is per appointment/plan).
- Attendance confirmation, reception no-show review, and repeat-no-show policies.
- Persistent Telegram conversation state (`conversations.workflow_*` exists but is unused; bot state is in-memory).
- AI tool-calling for booking actions; Gemini handles grounded answers and intent only.

## Deployment

- Backend: Render Blueprint `render.yaml` (`marina-smile-api`), start command `uvicorn app.main:app`.
- Dashboard: Vercel with root directory `dashboard`.
- Google credentials on Render are a secret file at `/etc/secrets/google-service-account.json`.
- Deployments follow `main`. Feature work should be merged to `main`; do not leave long-lived branches.
