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
```

`TELEGRAM_BOT_TOKEN` is optional. If set, `app.main` starts Telegram polling **and** the reminder worker inside the FastAPI lifespan. If unset, the API runs without either.

## Database migrations

There is no Supabase CLI configuration in the repo. Migrations are applied **manually in the Supabase SQL editor**, in filename order. Each file wraps its work in `begin; ... commit;`.

- Files `0001`–`0016` are the canonical base.
- Timestamped files (`20261020…`, `20261106…`, `20261109…`) are additive releases. Recent additions cover per-service schedules, live patient-identity reconciliation, and booking-plan cancellation. Never edit a migration that has already been applied; add a new timestamped file.
- The live database may differ from the canonical files. Previous work hit live-only constraints (`patients_check` requiring DOB/age, `contacts.phone_number NOT NULL`). When a write fails with `23514` or `23502`, inspect the live constraint and reconcile it with a new migration rather than editing applied ones.
- `supabase/tests/README.md` describes the SQL integrity/RLS suite; it requires a disposable project and is not run by `pytest`.

## Architecture

### Backend (`app/`)

- `app/main.py` — FastAPI app, CORS, lifespan-managed Telegram polling and notification worker.
- `app/api/routes/` — dashboard/API mutations. Critical writes go through PostgreSQL RPCs (`app/api/rpc.py`) with the caller's bearer token.
- `app/repositories/` — tenant-scoped reads.
- `app/operations/` — domain services. Two booking stacks coexist:
  - **Typed pipeline**: `booking.py`, `reschedule.py`, `cancellation.py`, `availability.py`, `slot_finder.py`. Used by scripts and the operational tools. It validates schedules, DB conflicts, and Calendar, then creates `pending -> confirmed` appointments.
  - **Telegram request pipeline**: `patient_booking.py` + `app/telegram/bot.py`. Creates `booking_requests`, then `reserve_appointment` / `confirm_appointment_reservation` RPCs. This is the live patient flow and supports multi-service bookings with per-service doctor/time rows in `booking_request_services` and `appointment_services`.
- `app/telegram/bot.py` — the patient state machine. Persistent menu: Book Appointment, My Appointments, Our Doctors, Dental Services, Ask Marina AI, Payments & Pricing, Help & Contact Reception.
  - `Book Appointment`: phone/email -> multi-service selection -> doctor eligible for all services -> live available slots from schedules/appointments/Calendar (no manual date entry) -> slot selection -> name/age/gender for new or incomplete patients -> cash-only confirmation with reference, patient ID, age, gender, doctor, services, and time.
  - `My Appointments`, payment history, and payment status: ask only for the booking phone/email, then show the patient's name, age, gender and grouped upcoming/past/cancelled appointments. Reschedule also lists live slots immediately.
  - Appointment actions: view details, reschedule (wired to `reschedule_appointment` with multi-service duration/service overrides), cancel appointment, cancel linked booking plan.
  - `Our Doctors`, `Dental Services`, `Ask Marina AI`, `Payments & Pricing` (prices, methods, history, payment status, refund guidance), `Help & Contact Reception`.
  - `Main menu` is intentionally absent from the persistent keyboard; a `🏠 Main menu` inline button exists only inside the booking flow.
- `app/telegram/notifications.py` — claims `outbound_notifications`, sends Telegram reminders, and calls `mark_due_appointments_served`.
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
- Contact identity is unique per clinic for normalized phone, email, and Telegram chat ID. One contact can have multiple patients (family), but the same phone/email cannot register a second distinct patient silently.
- Duplicate unserved bookings are blocked in the bot. Overdue never-checked-in visits are reconciled to `no_show`.
- 24-hour reminders are inserted as `outbound_notifications` when an appointment is confirmed and delivered by the polling worker.

### Dashboard (`dashboard/`)

- Next.js 16 + React 19 + TypeScript + Supabase SSR.
- Auth: Supabase Auth session -> active `staff_users` row -> role/permissions. No public registration.
- RLS governs direct reads. High-impact mutations go through the FastAPI API with the bearer token.
- Roles: `owner`, `doctor`, `receptionist`, `payment_verifier`.
- Payments page: online proof approval/rejection and cash "Mark paid".
- Most appointment/booking controls are intentionally read-only; do not assume the UI exposes every backend operation.
- `dashboard/AGENTS.md` is generated by `next dev` and warns that this Next.js version may differ from model training data. Read `dashboard/node_modules/next/dist/docs/` before writing Next.js code.

## Environment

Backend `.env` (see `.env.example`): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `GEMINI_API_KEY`, `DEFAULT_CLINIC_ID`, `TELEGRAM_BOT_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS`, `DASHBOARD_ALLOWED_ORIGINS`, demo settings.

Dashboard `.env.local` (see `dashboard/.env.example`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_BASE_URL`, plus server-only demo proxy variables.

Never expose `SUPABASE_SECRET_KEY`, Gemini keys, Google private keys, or `DEMO_API_TOKEN` through `NEXT_PUBLIC_*`. Backend scripts and the bot use the secret key and therefore bypass RLS; browser code must not.

## Verification expectations

- Python suite: 236 tests.
- Dashboard suite: 27 tests across 10 files.
- TypeScript must pass `npm run typecheck`.
- Tests do not prove live Supabase constraints, Calendar permissions, migrations, Telegram polling, or Render/Vercel deploys. Verify those separately against the hosted services.

## Known gaps (verified, not implemented)

- Multi-doctor/multi-visit booking plans, alternative-combination ranking, and partial-booking consent.
- Temporary slot holds and waiting-list management.
- Per-service cancellation inside a multi-service appointment (the current plan is one appointment row; cancellation is per appointment/plan).
- Attendance confirmation, 2-hour reminders, reception no-show review, and repeat-no-show policies.
- Persistent Telegram conversation state (`conversations.workflow_*` exists but is unused; bot state is in-memory).
- AI tool-calling for booking actions; Gemini handles grounded answers and intent only.

## Deployment

- Backend: Render Blueprint `render.yaml` (`marina-smile-api`), start command `uvicorn app.main:app`.
- Dashboard: Vercel with root directory `dashboard`.
- Google credentials on Render are a secret file at `/etc/secrets/google-service-account.json`.
- Deployments follow `main`. Feature work should be merged to `main`; do not leave long-lived branches.
