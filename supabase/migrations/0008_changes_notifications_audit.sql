begin;

create table public.appointment_change_requests (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    appointment_id uuid not null,
    requested_by_staff_id uuid,
    requested_by_actor_type text not null check (
        requested_by_actor_type in ('patient', 'doctor', 'owner', 'receptionist', 'payment_verifier', 'system')
    ),
    change_type text not null check (
        change_type in ('reschedule', 'doctor_cancelled', 'patient_reschedule', 'patient_cancelled', 'doctor_transfer')
    ),
    reason text,
    old_start_at timestamptz,
    old_end_at timestamptz,
    old_dentist_id uuid,
    old_service_id uuid,
    old_google_calendar_event_id text,
    status text not null default 'pending' check (
        status in ('pending', 'accepted', 'rejected', 'expired', 'applied', 'cancelled')
    ),
    created_at timestamptz not null default now(),
    resolved_at timestamptz,
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    foreign key (clinic_id, appointment_id)
        references public.appointments(clinic_id, id) on delete restrict,
    foreign key (clinic_id, requested_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    foreign key (clinic_id, old_dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    foreign key (clinic_id, old_service_id)
        references public.services(clinic_id, id) on delete restrict,
    check (
        (old_start_at is null and old_end_at is null)
        or (old_start_at is not null and old_end_at is not null and old_end_at > old_start_at)
    )
);

create table public.appointment_change_options (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    change_request_id uuid not null,
    dentist_id uuid not null,
    start_at timestamptz not null,
    end_at timestamptz not null,
    available_when_proposed boolean not null,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, change_request_id)
        references public.appointment_change_requests(clinic_id, id) on delete cascade,
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    check (end_at > start_at)
);

create table public.appointment_audit_log (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    appointment_id uuid,
    patient_id uuid,
    actor_type text not null check (
        actor_type in ('patient', 'doctor', 'owner', 'receptionist', 'payment_verifier', 'system')
    ),
    actor_id uuid,
    action text not null check (btrim(action) <> ''),
    old_values jsonb,
    new_values jsonb,
    reason text,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, appointment_id)
        references public.appointments(clinic_id, id) on delete restrict,
    foreign key (clinic_id, patient_id)
        references public.patients(clinic_id, id) on delete restrict
);

create table public.reminder_consents (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    contact_id uuid not null,
    whatsapp_reminders_enabled boolean not null default false,
    consent_source text,
    consented_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete cascade,
    unique (clinic_id, contact_id),
    check (not whatsapp_reminders_enabled or consented_at is not null)
);

create table public.outbound_notifications (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    contact_id uuid not null,
    booking_request_id uuid,
    appointment_id uuid,
    notification_type text not null check (btrim(notification_type) <> ''),
    channel text not null default 'whatsapp',
    reminder_number integer check (reminder_number is null or reminder_number > 0),
    scheduled_at timestamptz not null,
    sent_at timestamptz,
    status text not null default 'pending' check (
        status in ('pending', 'processing', 'sent', 'failed', 'cancelled')
    ),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict,
    foreign key (clinic_id, booking_request_id)
        references public.booking_requests(clinic_id, id) on delete restrict,
    foreign key (clinic_id, appointment_id)
        references public.appointments(clinic_id, id) on delete restrict
);

create unique index outbound_notifications_reminder_unique
    on public.outbound_notifications(booking_request_id, notification_type, reminder_number)
    where reminder_number is not null;

create table public.handoffs (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    contact_id uuid,
    conversation_id uuid,
    reason text not null check (btrim(reason) <> ''),
    summary text,
    priority text not null default 'normal' check (priority in ('low', 'normal', 'high', 'emergency')),
    status text not null default 'open' check (status in ('open', 'in_progress', 'resolved')),
    assigned_to_staff_id uuid,
    created_at timestamptz not null default now(),
    resolved_at timestamptz,
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict,
    foreign key (clinic_id, conversation_id)
        references public.conversations(clinic_id, id) on delete restrict,
    foreign key (clinic_id, assigned_to_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict
);

create table public.tool_executions (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid references public.clinics(id) on delete restrict,
    conversation_id uuid,
    tool_name text not null check (btrim(tool_name) <> ''),
    input_json jsonb,
    output_json jsonb,
    status text not null check (status in ('success', 'error')),
    duration_ms integer check (duration_ms is null or duration_ms >= 0),
    error_message text,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, conversation_id)
        references public.conversations(clinic_id, id) on delete set null,
    check (conversation_id is null or clinic_id is not null)
);

create trigger appointment_change_requests_set_updated_at before update on public.appointment_change_requests
for each row execute function public.set_updated_at();
create trigger reminder_consents_set_updated_at before update on public.reminder_consents
for each row execute function public.set_updated_at();
create trigger outbound_notifications_set_updated_at before update on public.outbound_notifications
for each row execute function public.set_updated_at();

comment on table public.appointment_audit_log is
    'Append-only lifecycle audit. Authenticated policies intentionally provide no UPDATE or DELETE access.';

commit;
