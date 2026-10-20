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
| `contacts` | Communication identity; unique clinic+WhatsApp ID | Reception/System writes; authorized care staff reads |
| `patients` | Durable patient identity; FK primary contact | Reception/System writes; assigned/authorized staff reads |
| `conversations` | Channel envelope plus durable workflow mode, step, resumable state, and active booking request | Reception/System writes; Reception/Owner reads |
| `messages` | Inbound/outbound messages; partial unique external ID | System/Reception writes; Reception/Owner reads |
| `booking_requests` | Non-reserving pre-payment workflow with clinic-scoped transport idempotency | System/Reception writes; verifier reads payment-relevant rows |
| `payments` | Normalized payment attempts with independent submission idempotency | System submits; authorized verifier RPC transitions; Owner/Receptionist/Payment Verifier reads |
| `appointments` | Real reserved intervals plus immutable patient-facing reference | Reservation/confirmation RPC or trusted backend writes; authorized operations read |
| `appointment_visits` | Queue state and idempotent check-in identity, one row per confirmed appointment | Confirmation trigger creates; check-in/queue RPCs update |
| `appointment_change_requests` | Actor-aware reschedule/cancel/transfer request | System/Reception writes; assigned/authorized staff reads |
| `appointment_change_options` | Non-reserving proposed alternatives | System/Reception writes; request-authorized staff reads |
| `appointment_audit_log` | Append-only action history | Trusted operation/authenticated actor inserts; no normal update/delete |
| `reminder_consents` | Contact reminder choice and timestamps | Reception/System writes; Reception/Owner reads |
| `outbound_notifications` | Concurrent-worker-safe delivery queue with logical event key and template data | System writes/claims; Reception/Owner observes |
| `refund_requests` | Cancellation-independent request, review, approval, and payout lifecycle | Trusted server requests; authorized Receptionist reviews/pays; Owner supervises |
| `handoffs` | Human escalation queue | System/Reception writes; Reception/Owner manages |
| `tool_executions` | Sanitized tool inputs, outputs, duration, status | Trusted backend writes; Owner reads |
| `knowledge_documents` | Versioned RAG source metadata, unique checksum per clinic | Owner/System ingests; server retrieval uses ready rows |
| `knowledge_chunks` | Page-aware text and `vector(768)` embeddings | Owner/System ingests; server retrieval through RPC |

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
