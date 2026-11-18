# Table catalogue

Every public table has RLS enabled. `clinic_id` is the ownership key unless the
row is the clinic itself. Service-role backend jobs bypass RLS but still face
foreign keys, checks, triggers, uniqueness, and exclusion constraints.

| Table | Purpose and important relationships | Lifecycle / writer / reader |
|---|---|---|
| `clinics` | Tenant configuration; PK `id`, unique `slug` | Owner updates; active staff reads |
| `staff_users` | Dashboard identity; FK `auth.users`, clinic, optional dentist | Service/Owner provisions; self and Owner read |
| `staff_permission_overrides` | Per-user permission exception; unique staff+permission | Owner manages; user may read own override |
| `dentists` | Durable clinical resource; deactivation actor FK | Owner manages; clinic staff reads; never normally deleted |
| `doctor_status_change_requests` | Owner-approved deactivation workflow | Doctor requests; Owner reviews/applies |
| `services` | Structured duration, price, currency, payment policy | Owner manages; clinic staff reads |
| `dentist_services` | Structural dentist qualification | Owner manages; clinic staff reads |
| `dentist_schedules` | Weekly local-time working ranges | Owner/Receptionist or own Doctor manages |
| `schedule_overrides` | Time-bounded unavailable/extra availability | Authorized scheduling staff writes and reads |
| `contacts` | Communication identity: unique normalized phone/email per clinic, Telegram chat binding, legacy `whatsapp_id` placeholder | Telegram bot/System writes; authorized care staff reads |
| `patients` | Durable patient identity; FK primary contact | Reception/System writes; assigned/authorized staff reads |
| `conversations` | **Reserved.** Channel envelope plus durable workflow columns; no application code writes it (bot state is in memory) | Unused today; do not build features that assume it is populated |
| `messages` | **Reserved.** Inbound/outbound message history; no application code writes it | Unused today |
| `booking_requests` | Non-reserving pre-payment workflow with clinic-scoped transport idempotency | System/Reception writes; verifier reads payment-relevant rows |
| `payments` | Normalized payment attempts with independent submission idempotency | System submits; authorized verifier RPC transitions; Owner/Receptionist/Payment Verifier reads |
| `appointments` | Real reserved intervals plus immutable patient-facing reference | Reservation/confirmation RPC or trusted backend writes; authorized operations read |
| `appointment_visits` | Queue state and idempotent check-in identity, one row per confirmed appointment | Confirmation trigger creates; check-in/queue RPCs update |
| `appointment_change_requests` | **Reserved.** Actor-aware reschedule/cancel/transfer request with old-appointment snapshots (including `old_service_id`, `old_google_calendar_event_id`) | No writer today; read-only realtime subscription in the dashboard |
| `appointment_change_options` | **Reserved.** Non-reserving proposed alternatives | No writer today |
| `appointment_audit_log` | Append-only action history | Trusted operation/authenticated actor inserts; no normal update/delete |
| `reminder_consents` | **Reserved.** Contact reminder choice and timestamps | No application writer today; the reminder worker does not consult it |
| `outbound_notifications` | Concurrent-worker-safe delivery queue with logical event key and template data; `channel` defaults to `telegram` | DB triggers schedule; the Telegram worker claims and delivers |
| `refund_requests` | Cancellation-independent request, review, approval, and payout lifecycle | RPC-backed (`request_refund`, `approve_refund`, `reject_refund`, `record_refund_payout`); no dashboard/API caller yet |
| `handoffs` | Human escalation queue created by the Help menu | `patient_handoff.py` writes; Reception/Owner manages |
| `tool_executions` | **Reserved.** Sanitized tool inputs, outputs, duration, status | No writer today; Gemini has no tool execution |
| `knowledge_documents` | Versioned RAG source metadata, unique checksum per clinic | Owner/System ingests; server retrieval uses ready rows |
| `knowledge_chunks` | Page-aware text and `vector(768)` embeddings | Owner/System ingests; server retrieval through RPC |
| `booking_request_services` | Per-service doctor, price, and time lines for a booking request | Telegram/System writes; authorized staff reads |
| `appointment_services` | Per-service lines copied onto the confirmed appointment | Booking finalization writes; authorized staff reads |
| `clinic_holidays` | Dated closures, manual or Google-synchronized (`source`, `google_event_id`) | Google sync job writes; booking and staff read |
| `clinic_holiday_sync` | Sync token, coverage, lease, and last error per clinic | Google sync job writes; owners/schedule managers may read |
| `demo_query_events` | Per-visitor demo quota events | Written only by the `consume_demo_query_quota` security-definer RPC |
| `doctor_status_change_requests` | Owner-approved deactivation workflow | Doctor requests; Owner reviews/applies |

## Legacy WhatsApp-era fields

`clinics.whatsapp_phone_number_id`, `clinics.whatsapp_business_account_id`, and
`contacts.whatsapp_id` predate the Telegram implementation. No WhatsApp
integration code exists in this repository. They are retained because live data
and the privacy tombstone logic depend on `whatsapp_id`; do not drop them.

## Live schema notes

The live project is authoritative for constraints. Two differences from the
base migration files are intentional and must not be "fixed" by weakening the
database:

- `appointments.booking_request_id` and `appointments.patient_id` are NOT NULL
  live, so every appointment must belong to a booking request and a patient.
- `appointments.idempotency_key` is nullable live, although `0006` declares it
  NOT NULL.

## Important foreign-key behavior

Durable history uses `ON DELETE RESTRICT`: appointments, payments, patients,
dentists, and audit rows cannot disappear because an operational entity becomes
inactive. Child-only data uses narrow cascades: document deletion removes its
chunks; conversation deletion removes messages; a change request removes only
its unaccepted proposal options. Composite foreign keys prevent a row from
linking records owned by different clinics.

## Important uniqueness and concurrency constraints

- `contacts(clinic_id, whatsapp_id)` identifies one communication identity.
- `patients(clinic_id, patient_number)` identifies one clinic patient number.
- `messages.external_message_id` is unique when present.
- `booking_requests(clinic_id, idempotency_key)` is unique when present.
- `payments` permits many attempts per booking request.
- `payments(clinic_id, submission_idempotency_key)` is unique when present.
- `appointments.booking_request_id` is unique when present, preventing two real
  appointments from one workflow.
- `appointments.idempotency_key` is independently unique.
- `appointments.appointment_reference` is globally unique, immutable, and
  generated from a concurrency-safe sequence.
- Active appointment ranges use `[start_at,end_at)` so overlaps are rejected and
  adjacent intervals remain valid.
- Reminder number/type/request is unique to prevent duplicate reminders.
- `outbound_notifications(clinic_id, event_key)` prevents duplicate logical
  state-change notifications.
- `appointment_visits.appointment_id` is unique.
- Refund request and payout idempotency keys are independently unique per
  clinic, and a payment/appointment pair has at most one refund request.
- RAG documents are unique by clinic and checksum; chunks by document and index.
