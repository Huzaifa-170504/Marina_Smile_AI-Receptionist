begin;

set local search_path = public, extensions;

create table public.appointments (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    patient_id uuid not null,
    contact_id uuid not null,
    booking_request_id uuid,
    payment_id uuid,
    service_id uuid not null,
    dentist_id uuid not null,
    start_at timestamptz not null,
    end_at timestamptz not null,
    status text not null default 'pending' check (
        status in ('pending', 'confirmed', 'cancelled', 'completed', 'no_show')
    ),
    google_calendar_event_id text,
    notes text,
    idempotency_key uuid not null unique,
    cancelled_at timestamptz,
    cancelled_by_staff_id uuid,
    cancellation_reason text,
    cancellation_source text check (
        cancellation_source is null
        or cancellation_source in ('patient', 'doctor', 'owner', 'receptionist', 'system')
    ),
    completed_at timestamptz,
    completed_by_staff_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    foreign key (clinic_id, patient_id)
        references public.patients(clinic_id, id) on delete restrict,
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict,
    foreign key (clinic_id, booking_request_id, patient_id, contact_id, service_id)
        references public.booking_requests(clinic_id, id, patient_id, contact_id, service_id)
        on delete restrict,
    foreign key (clinic_id, payment_id, booking_request_id, patient_id)
        references public.payments(clinic_id, id, booking_request_id, patient_id)
        on delete restrict,
    foreign key (clinic_id, service_id)
        references public.services(clinic_id, id) on delete restrict,
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    foreign key (clinic_id, dentist_id, service_id)
        references public.dentist_services(clinic_id, dentist_id, service_id)
        on delete restrict,
    foreign key (clinic_id, cancelled_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    foreign key (clinic_id, completed_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check (end_at > start_at),
    check (payment_id is null or booking_request_id is not null),
    check ((status <> 'cancelled') or cancelled_at is not null),
    check ((status <> 'completed') or completed_at is not null)
);

create unique index appointments_one_per_booking_request
    on public.appointments(booking_request_id)
    where booking_request_id is not null;

alter table public.appointments
    add constraint appointments_no_active_overlap
    exclude using gist (
        dentist_id with =,
        tstzrange(start_at, end_at, '[)') with &&
    )
    where (status in ('pending', 'confirmed'));

create or replace function private.validate_appointment_write()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    service_requires_payment boolean;
    request_row public.booking_requests%rowtype;
    payment_status text;
begin
    if tg_op = 'INSERT' and new.status <> 'pending' then
        raise exception using message = 'A new appointment must start as pending', errcode = 'check_violation';
    end if;

    if tg_op = 'UPDATE' and (
        new.clinic_id is distinct from old.clinic_id
        or new.patient_id is distinct from old.patient_id
        or new.contact_id is distinct from old.contact_id
        or new.booking_request_id is distinct from old.booking_request_id
        or new.payment_id is distinct from old.payment_id
        or new.service_id is distinct from old.service_id
        or new.idempotency_key is distinct from old.idempotency_key
    ) then
        raise exception using message = 'Appointment identity and financial links are immutable', errcode = 'check_violation';
    end if;

    select payment_required
      into service_requires_payment
      from public.services
     where id = new.service_id
       and clinic_id = new.clinic_id;

    if service_requires_payment is null then
        raise exception using message = 'Appointment service is invalid for clinic', errcode = '23503';
    end if;

    if new.booking_request_id is not null then
        select *
          into request_row
          from public.booking_requests
         where id = new.booking_request_id
           and clinic_id = new.clinic_id;

        if not found then
            raise exception using message = 'Booking request is invalid for clinic', errcode = '23503';
        end if;

        if request_row.status not in ('payment_verified', 'awaiting_slot_selection', 'converted_to_appointment')
           and not request_row.payment_exempt then
            raise exception using message = 'Booking request is not payment-cleared', errcode = 'check_violation';
        end if;
    end if;

    if new.payment_id is not null then
        select status
          into payment_status
          from public.payments
         where id = new.payment_id
           and clinic_id = new.clinic_id;
        if payment_status is distinct from 'verified' then
            raise exception using message = 'Appointment payment is not verified', errcode = 'check_violation';
        end if;
    end if;

    if service_requires_payment
       and (
           new.booking_request_id is null
           or (
               not coalesce(request_row.payment_exempt, false)
               and new.payment_id is null
           )
       ) then
        raise exception using message = 'Payment-required service needs verified payment or owner exemption', errcode = 'check_violation';
    end if;

    if tg_op = 'UPDATE' and new.status is distinct from old.status then
        if not (
            (old.status = 'pending' and new.status in ('confirmed', 'cancelled'))
            or (old.status = 'confirmed' and new.status in ('cancelled', 'completed', 'no_show'))
        ) then
            raise exception using
                message = format('Invalid appointment transition: %s -> %s', old.status, new.status),
                errcode = 'check_violation';
        end if;
    end if;

    if new.status = 'cancelled' and new.cancelled_at is null then
        new.cancelled_at := pg_catalog.now();
    end if;
    if new.status = 'completed' and new.completed_at is null then
        new.completed_at := pg_catalog.now();
    end if;
    if new.status = 'confirmed' and nullif(pg_catalog.btrim(new.google_calendar_event_id), '') is null then
        raise exception using message = 'A confirmed appointment requires a Google Calendar event ID', errcode = 'check_violation';
    end if;
    if new.status = 'cancelled' and (
        nullif(pg_catalog.btrim(new.cancellation_reason), '') is null
        or new.cancellation_source is null
    ) then
        raise exception using message = 'A cancelled appointment requires a reason and source', errcode = 'check_violation';
    end if;
    if new.status = 'completed' and new.completed_by_staff_id is null then
        raise exception using message = 'A completed appointment requires the responsible staff member', errcode = 'check_violation';
    end if;

    return new;
end;
$$;

create trigger appointments_validate_write
before insert or update on public.appointments
for each row execute function private.validate_appointment_write();

create trigger appointments_set_updated_at
before update on public.appointments
for each row execute function public.set_updated_at();

comment on column public.appointments.idempotency_key is
    'Retry identity for the appointment reservation operation; distinct from booking_request_id.';

commit;
