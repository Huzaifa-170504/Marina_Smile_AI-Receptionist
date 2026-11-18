# Database architecture

## Scope and source-of-truth boundaries

PostgreSQL is authoritative for clinics, staff identity, permissions, patient
identity, payment state, booking workflow, appointment reservation, queue state,
auditing, notifications, and RAG metadata. The active-appointment exclusion
constraint is the final concurrency boundary; availability reads are advisory.

Google Calendar is a synchronized operational view. The booking sequence remains
`pending database reservation -> Calendar event -> confirmed database state`.
Calendar failure cancels the pending row. A failed final confirmation requires
Calendar compensation. Gemini may extract language but must not decide or mutate
database state.

All operational timestamps use `timestamptz`. Weekly schedules use `time without
time zone`, interpreted in `clinics.timezone`. Every tenant-owned relationship
uses `clinic_id`, with composite foreign keys where they prevent cross-clinic
references.

## Identity model

- `contacts` represents a reachable identity for a patient: a normalized phone
  number, an email address, a Telegram chat binding, and a legacy `whatsapp_id`
  placeholder. Phone and email are unique per clinic; one Telegram chat may be
  linked to more than one contact (for example family members).
- `patients` represents the durable person receiving care. A contact may be the
  primary contact for more than one patient, such as dependants.
- `dentists` represents a clinical scheduling resource.
- `staff_users` links a dashboard identity to `auth.users`; passwords remain in
  Supabase Auth. A dentist does not require a login.
- Cancelling a booking replaces contact identifiers with a non-identifying
  `deleted:<contact id>` tombstone for `whatsapp_id` and clears name, phone,
  email, and Telegram binding, so the phone number can be registered again.

## Cash-first booking and payments

A `booking_request` is a workflow record and never reserves time by itself.
`payments` contains attempts, so rejected or resubmitted proofs create
additional rows rather than overwriting history.

- **Cash (the live Telegram default):** `create_booking_request` inserts the
  request directly with `status = 'awaiting_slot_selection'` and
  `payment_mode = 'cash'`. The reservation RPC then creates the appointment with
  `payment_status = 'unpaid'`; staff settle it later with
  `mark_cash_payment_paid`.
- **Online:** the request starts at `awaiting_payment`. A receipt is uploaded to
  the private `payment-proofs` bucket, `verify_payment` advances the request to
  `payment_verified`, and the backend then finalizes the reservation. Only an
  owner-authorized exemption can bypass a verified payment.

`appointments.booking_request_id` is the workflow foreign key and
`appointments.idempotency_key` independently protects request retries. The live
table also requires `patient_id` and `booking_request_id`, and enforces clinic
hours, breaks, closed weekdays, and holidays in
`private.validate_appointment_write()`.

The atomic database helpers are:

1. `verify_payment` conditionally verifies one submitted payment and advances
   the booking request.
2. `reserve_appointment` locks the cleared booking request, rejects a duplicate
   active booking for the same service and doctor, and inserts a pending
   appointment. PostgreSQL rejects an overlap under concurrency.
3. The backend creates the Google Calendar event.
4. `confirm_appointment_reservation` confirms the appointment and marks the
   booking request converted in one transaction.

## Operational workflows

`appointment_visits` separates the clinic queue from appointment booking status.
`transition_queue_state` validates queue movement and atomically completes or
marks no-show on the appointment when required. Schedule calculation applies
weekly schedules, then overrides, active appointments, and finally Calendar
conflicts.

Change requests and proposed options do not reserve time. Acceptance must rerun
availability and use the appointment exclusion constraint. Audit rows store the
actor, reason, and old/new values without secrets.

## Migration order

| Order | File | Responsibility |
|---:|---|---|
| 1 | `0001_extensions_and_helpers.sql` | Extensions, private schema, shared timestamp trigger |
| 2 | `0002_clinic_staff_auth.sql` | Clinic, Auth-linked staff, permission overrides |
| 3 | `0003_dentists_services_schedules.sql` | Dentists, services, qualifications, weekly schedules |
| 4 | `0004_contacts_patients_messaging.sql` | Contacts, patients, conversations, messages |
| 5 | `0005_booking_requests_payments.sql` | Pre-payment workflow and payment attempts |
| 6 | `0006_appointments_and_overlap.sql` | Reserved appointments, payment gate, overlap protection |
| 7 | `0007_queue_and_schedule_overrides.sql` | Queue/visit state and schedule exceptions |
| 8 | `0008_changes_notifications_audit.sql` | Changes, notifications, audit, consent, handoffs, tool logs |
| 9 | `0009_rag.sql` | RAG documents, 768-dimensional chunks, tenant-filtered RPC |
| 10 | `0010_rls_helpers.sql` | Non-exposed authorization helpers |
| 11 | `0011_rls_policies.sql` | Table grants and RLS policies |
| 12 | `0012_storage_setup.sql` | Private Storage buckets and object policies |
| 13 | `0013_realtime_setup.sql` | Limited Realtime publication |
| 14 | `0014_indexes_and_validation.sql` | Indexes, safety triggers, transactional RPCs |
| 15 | `0015_live_reconciliation.sql` | Data-preserving reconciliation for verified live grants, RLS, RPC, Storage, Realtime, and index drift |
| 16 | `0016_patient_operations_foundation.sql` | Durable workflow state, patient references, refunds, check-in, and role-separated queue operations |

Additive timestamped releases follow `0016` in filename order. They must be
applied after the base set and must never be edited once applied:

| File | Responsibility |
|---|---|
| `20261020083343_patient_operations_coalesce_compatibility.sql` | COALESCE compatibility fixes |
| `20261020083413_demo_query_rate_limit.sql` | `demo_query_events` and quota RPC |
| `20261106104639_add_patient_email_for_telegram.sql` | Patient email identity |
| `20261106150000_complete_patient_booking_workflow.sql` | Multi-service booking, request services, notification records |
| `20261106180000_per_service_schedule.sql` | Per-service doctor and time rows |
| `20261106190000_reconcile_patient_identity_requirements.sql` | Live identity constraint reconciliation |
| `20261109120000_allow_booking_plan_cancellation.sql` | Plan-level cancellation |
| `20261109130000_drop_legacy_appointment_payment_gate.sql` | Removes the legacy cash-blocking trigger |
| `20261109140000_cancel_booking_privacy_cleanup.sql` | `anonymize_cancelled_booking` |
| `20261109150000_fix_appointment_audit_trigger_security.sql` | Audit trigger security definer fix |
| `20261109160000_allow_multiple_contacts_per_telegram.sql` | Multiple contacts per chat |
| `20261109170000_feedback_notification_trigger.sql` | Post-completion feedback |
| `20261109180000_appointment_notification_scheduler.sql` | DB-owned reminder scheduling |
| `20261109190000_reminder_timing_rules.sql` | 24h/2h reminder timing rules |
| `20261117100000_booking_duplicate_guard.sql` | Advisory-locked duplicate service/doctor guard |
| `20261117110000_clinic_hours_and_holidays.sql` | Clinic hours, holidays, and validation trigger |
| `20261117120000_google_holiday_sync.sql` | Google holiday sync state, lease, and RPCs |
| `20261117130000_anonymize_contact_whatsapp_id.sql` | Anonymized `whatsapp_id` tombstones |
| `20261118120000_change_request_snapshot_columns.sql` | Re-adds missing change-request snapshot columns and index |
| `20261118130000_holiday_sync_owner_only.sql` | Restricts holiday sync diagnostics to owners/schedule managers |
| `20261118140000_notification_channel_default.sql` | Telegram as the notification channel default |

Seed data is separate in `supabase/seeds/seed_demo_marina_smile.sql` and contains
no patient, contact, payment, proof, or staff-authentication data.

## Current application compatibility

The live patient flow is the Telegram request pipeline:
`patient_booking.py` resolves or registers the patient, creates the booking
request and its service lines, then calls `reserve_appointment` and
`confirm_appointment_reservation`.

The older **typed direct-booking pipeline** (`app/operations/booking.py`,
`scripts/book_appointment.py`) is not live-compatible. It inserts appointments
without `patient_id`, without a real `booking_requests` parent, and without the
payment-cleared booking request that the live validation trigger requires. It is
retained for isolated tests and future adapter work; use the Telegram flow or the
reservation RPCs for real bookings. The database does not weaken its integrity
rules to preserve the legacy misuse.

## Deployment risks

- These are from-scratch migrations, not an in-place upgrade for the prior
  project.
- The target Supabase project identity must be verified before applying them.
- Storage and Realtime migrations require the standard Supabase `storage`
  schema and `supabase_realtime` publication.
- Staff fixtures require real `auth.users` records created through Supabase Auth.
- Google Calendar IDs, WhatsApp IDs, phone numbers, and secrets are not seeded.
- RLS should be exercised with real JWTs for each role after staff accounts are
  created.
