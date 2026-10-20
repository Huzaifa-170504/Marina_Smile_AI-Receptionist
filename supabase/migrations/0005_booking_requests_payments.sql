begin;

create table public.booking_requests (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    patient_id uuid not null,
    contact_id uuid not null,
    service_id uuid not null,
    preferred_dentist_id uuid,
    preferred_date date,
    preferred_window_start timestamptz,
    preferred_window_end timestamptz,
    status text not null default 'awaiting_patient_info' check (
        status in (
            'awaiting_patient_info', 'awaiting_payment', 'payment_submitted',
            'payment_rejected', 'payment_verified', 'awaiting_slot_selection',
            'converted_to_appointment', 'expired', 'cancelled'
        )
    ),
    payment_exempt boolean not null default false,
    payment_exempt_reason text,
    payment_exempted_by_staff_id uuid,
    reminder_count integer not null default 0 check (reminder_count >= 0),
    last_reminder_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, id, patient_id),
    unique (clinic_id, id, patient_id, contact_id, service_id),
    foreign key (clinic_id, patient_id)
        references public.patients(clinic_id, id) on delete restrict,
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict,
    foreign key (clinic_id, service_id)
        references public.services(clinic_id, id) on delete restrict,
    foreign key (clinic_id, preferred_dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    foreign key (clinic_id, payment_exempted_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check (
        (preferred_window_start is null and preferred_window_end is null)
        or (
            preferred_window_start is not null
            and preferred_window_end is not null
            and preferred_window_end > preferred_window_start
        )
    ),
    check (
        (not payment_exempt and payment_exempt_reason is null and payment_exempted_by_staff_id is null)
        or (
            payment_exempt
            and nullif(btrim(payment_exempt_reason), '') is not null
            and payment_exempted_by_staff_id is not null
        )
    )
);

create table public.payments (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    booking_request_id uuid not null,
    patient_id uuid not null,
    amount_expected numeric(12,2) check (amount_expected is null or amount_expected >= 0),
    amount_verified numeric(12,2) check (amount_verified is null or amount_verified >= 0),
    currency text not null default 'AED' check (currency ~ '^[A-Z]{3}$'),
    proof_storage_path text,
    status text not null default 'awaiting' check (
        status in (
            'awaiting', 'submitted', 'verified', 'rejected',
            'refund_required', 'refund_processing', 'refunded'
        )
    ),
    submitted_at timestamptz,
    verified_by_staff_id uuid,
    verified_at timestamptz,
    rejection_reason text,
    verifier_notes text,
    refund_reason text,
    refund_completed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, id, booking_request_id, patient_id),
    foreign key (clinic_id, booking_request_id, patient_id)
        references public.booking_requests(clinic_id, id, patient_id) on delete restrict,
    foreign key (clinic_id, verified_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check ((status <> 'submitted') or (proof_storage_path is not null and submitted_at is not null)),
    check ((status <> 'verified') or (verified_at is not null and verified_by_staff_id is not null)),
    check ((status <> 'rejected') or nullif(btrim(rejection_reason), '') is not null),
    check ((status <> 'refunded') or refund_completed_at is not null)
);

create unique index payments_proof_storage_path_unique
    on public.payments(proof_storage_path)
    where proof_storage_path is not null;

create trigger booking_requests_set_updated_at
before update on public.booking_requests
for each row execute function public.set_updated_at();

create trigger payments_set_updated_at
before update on public.payments
for each row execute function public.set_updated_at();

comment on table public.payments is
    'Each row is one payment attempt; a booking request may have multiple attempts.';

commit;
