begin;

create table public.dentists (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    name text not null check (btrim(name) <> ''),
    specialty text,
    bio text,
    active boolean not null default true,
    deactivated_at timestamptz,
    deactivated_by_staff_id uuid,
    deactivation_reason text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, name),
    foreign key (clinic_id, deactivated_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check (
        (active and deactivated_at is null)
        or (not active and deactivated_at is not null)
    )
);

alter table public.staff_users
    add constraint staff_users_doctor_tenant_fk
    foreign key (clinic_id, doctor_id)
    references public.dentists(clinic_id, id) on delete restrict;

create unique index staff_users_one_login_per_doctor
    on public.staff_users(doctor_id)
    where doctor_id is not null;

create table public.doctor_status_change_requests (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    dentist_id uuid not null,
    requested_by_staff_id uuid not null,
    requested_status text not null check (requested_status = 'inactive'),
    reason text not null check (btrim(reason) <> ''),
    status text not null default 'pending' check (
        status in ('pending', 'approved', 'rejected', 'cancelled', 'applied')
    ),
    reviewed_by_staff_id uuid,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    foreign key (clinic_id, requested_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    foreign key (clinic_id, reviewed_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check ((reviewed_at is null) = (reviewed_by_staff_id is null))
);

create table public.services (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    name text not null check (btrim(name) <> ''),
    description text,
    duration_minutes integer not null check (duration_minutes > 0),
    price_from numeric(12,2) check (price_from is null or price_from >= 0),
    currency text not null default 'AED' check (currency ~ '^[A-Z]{3}$'),
    payment_required boolean not null default true,
    booking_deposit_amount numeric(12,2)
        check (booking_deposit_amount is null or booking_deposit_amount >= 0),
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, name)
);

create table public.dentist_services (
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    dentist_id uuid not null,
    service_id uuid not null,
    created_at timestamptz not null default now(),
    primary key (dentist_id, service_id),
    unique (clinic_id, dentist_id, service_id),
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete cascade,
    foreign key (clinic_id, service_id)
        references public.services(clinic_id, id) on delete cascade
);

create table public.dentist_schedules (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    dentist_id uuid not null,
    weekday smallint not null check (weekday between 0 and 6),
    start_time time without time zone not null,
    end_time time without time zone not null,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete cascade,
    check (end_time > start_time),
    unique (dentist_id, weekday, start_time, end_time)
);

create trigger dentists_set_updated_at
before update on public.dentists
for each row execute function public.set_updated_at();

create trigger doctor_status_change_requests_set_updated_at
before update on public.doctor_status_change_requests
for each row execute function public.set_updated_at();

create trigger services_set_updated_at
before update on public.services
for each row execute function public.set_updated_at();

commit;
