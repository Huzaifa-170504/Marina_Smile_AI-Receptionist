begin;

create table public.clinics (
    id uuid primary key default gen_random_uuid(),
    name text not null check (btrim(name) <> ''),
    slug text not null unique check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
    business_type text not null default 'dental_clinic',
    timezone text not null default 'Asia/Dubai',
    address text,
    emergency_phone text,
    languages jsonb not null default '["English", "Arabic"]'::jsonb
        check (jsonb_typeof(languages) = 'array'),
    whatsapp_phone_number_id text,
    google_calendar_id text,
    staff_alert_phone text,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, active)
);

create table public.staff_users (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    auth_user_id uuid not null unique references auth.users(id) on delete cascade,
    doctor_id uuid,
    full_name text not null check (btrim(full_name) <> ''),
    email text not null check (btrim(email) <> ''),
    role text not null check (
        role in ('owner', 'doctor', 'receptionist', 'payment_verifier')
    ),
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, email),
    check ((role = 'doctor') = (doctor_id is not null))
);

create table public.staff_permission_overrides (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    staff_user_id uuid not null,
    permission text not null check (btrim(permission) <> ''),
    allowed boolean not null,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, staff_user_id)
        references public.staff_users(clinic_id, id) on delete cascade,
    unique (staff_user_id, permission)
);

create trigger clinics_set_updated_at
before update on public.clinics
for each row execute function public.set_updated_at();

create trigger staff_users_set_updated_at
before update on public.staff_users
for each row execute function public.set_updated_at();

commit;
