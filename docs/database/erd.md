# Entity relationship diagram

```mermaid
erDiagram
    CLINICS ||--o{ STAFF_USERS : employs
    AUTH_USERS ||--o| STAFF_USERS : authenticates
    CLINICS ||--o{ DENTISTS : owns
    STAFF_USERS o|--o| DENTISTS : doctor_login
    STAFF_USERS ||--o{ STAFF_PERMISSION_OVERRIDES : has
    DENTISTS ||--o{ DOCTOR_STATUS_CHANGE_REQUESTS : subject

    CLINICS ||--o{ SERVICES : offers
    DENTISTS ||--o{ DENTIST_SERVICES : qualified_for
    SERVICES ||--o{ DENTIST_SERVICES : provided_by
    DENTISTS ||--o{ DENTIST_SCHEDULES : works
    DENTISTS ||--o{ SCHEDULE_OVERRIDES : exceptions

    CLINICS ||--o{ CONTACTS : communicates_with
    CONTACTS ||--o{ PATIENTS : primary_for
    CONTACTS ||--o{ CONVERSATIONS : starts
    CONVERSATIONS ||--o{ MESSAGES : contains
    BOOKING_REQUESTS o|--o{ CONVERSATIONS : active_in

    PATIENTS ||--o{ BOOKING_REQUESTS : requests
    CONTACTS ||--o{ BOOKING_REQUESTS : communicates
    SERVICES ||--o{ BOOKING_REQUESTS : requests
    BOOKING_REQUESTS ||--o{ PAYMENTS : attempts

    BOOKING_REQUESTS o|--o| APPOINTMENTS : converts_to
    PAYMENTS o|--o| APPOINTMENTS : clears
    PATIENTS ||--o{ APPOINTMENTS : receives
    CONTACTS ||--o{ APPOINTMENTS : owns_request
    SERVICES ||--o{ APPOINTMENTS : scheduled_service
    DENTISTS ||--o{ APPOINTMENTS : assigned
    APPOINTMENTS ||--|| APPOINTMENT_VISITS : queue_record
    APPOINTMENTS ||--o| REFUND_REQUESTS : refund_subject
    PAYMENTS ||--o| REFUND_REQUESTS : refunded_payment
    PATIENTS ||--o{ REFUND_REQUESTS : requests
    STAFF_USERS o|--o{ REFUND_REQUESTS : reviews_or_pays

    APPOINTMENTS ||--o{ APPOINTMENT_CHANGE_REQUESTS : change_history
    APPOINTMENT_CHANGE_REQUESTS ||--o{ APPOINTMENT_CHANGE_OPTIONS : proposes
    APPOINTMENTS o|--o{ APPOINTMENT_AUDIT_LOG : audited
    PATIENTS o|--o{ APPOINTMENT_AUDIT_LOG : subject

    CONTACTS ||--o{ REMINDER_CONSENTS : chooses
    CONTACTS ||--o{ OUTBOUND_NOTIFICATIONS : receives
    BOOKING_REQUESTS o|--o{ OUTBOUND_NOTIFICATIONS : triggers
    APPOINTMENTS o|--o{ OUTBOUND_NOTIFICATIONS : triggers
    CONVERSATIONS o|--o{ HANDOFFS : escalates
    CONVERSATIONS o|--o{ TOOL_EXECUTIONS : observes

    CLINICS ||--o{ KNOWLEDGE_DOCUMENTS : owns
    KNOWLEDGE_DOCUMENTS ||--o{ KNOWLEDGE_CHUNKS : splits_into
```

`AUTH_USERS` is `auth.users`, managed by Supabase Auth. Storage objects live in
the Supabase `storage` schema and are linked to tenant/workflow records by their
private object path rather than a public foreign key.
