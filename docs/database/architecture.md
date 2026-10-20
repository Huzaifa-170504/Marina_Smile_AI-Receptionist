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

- `contacts` represents a communication identity, such as a WhatsApp number.
- `patients` represents the durable person receiving care. A contact may be the
  primary contact for more than one patient, such as dependants.
- `dentists` represents a clinical scheduling resource.
- `staff_users` links a dashboard identity to `auth.users`; passwords remain in
  Supabase Auth. A dentist does not require a login.

## Payment-gated booking

A `booking_request` is a workflow record and never reserves time. `payments`
contains attempts, so rejected/resubmitted proofs create additional rows rather
than overwriting history. A real appointment can be inserted only when a
payment-required service has either a verified payment or an owner-authorized
exemption. `appointments.booking_request_id` is the workflow foreign key;
`appointments.idempotency_key` independently protects request retries.

The atomic database helpers are:

1. `verify_payment` conditionally verifies one submitted payment and advances
   the booking request.
2. `reserve_appointment` locks the cleared booking request and inserts a pending
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

Seed data is separate in `supabase/seeds/seed_demo_marina_smile.sql` and contains
no patient, contact, payment, proof, or staff-authentication data.

## Current application compatibility

Read paths for clinics, dentists, services, schedules, contacts, appointments,
and RAG retain their existing conceptual tables and columns. The new production
contract intentionally requires `patient_id` and payment clearance for real
appointments. It also corrects the old overloaded retry field:

- old application meaning: `booking_request_id` was a retry UUID;
- target meaning: `booking_request_id` references the workflow record;
- target retry field: `idempotency_key`.

The current direct-booking Python operation therefore requires a follow-up
adapter to create/resolve a patient and booking request and call the reservation
RPC before it can write to this target schema. The database does not weaken its
integrity rules to preserve the legacy misuse.

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
