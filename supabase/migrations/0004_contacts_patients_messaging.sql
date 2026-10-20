begin;

create table public.contacts (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    whatsapp_id text not null check (btrim(whatsapp_id) <> ''),
    phone_number text,
    name text,
    preferred_language text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, whatsapp_id)
);

create table public.patients (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    primary_contact_id uuid not null,
    patient_number text not null check (btrim(patient_number) <> ''),
    full_name text not null check (btrim(full_name) <> ''),
    date_of_birth date,
    age_at_registration integer check (age_at_registration is null or age_at_registration >= 0),
    gender text not null check (gender in ('male', 'female', 'other')),
    guardian_name text,
    guardian_contact text,
    guardian_relationship text,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, patient_number),
    foreign key (clinic_id, primary_contact_id)
        references public.contacts(clinic_id, id) on delete restrict,
    check (date_of_birth is null or date_of_birth <= current_date),
    check (
        (guardian_name is null and guardian_contact is null and guardian_relationship is null)
        or (guardian_name is not null and guardian_contact is not null)
    )
);

create table public.conversations (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    contact_id uuid not null,
    channel text not null default 'whatsapp',
    status text not null default 'open' check (status in ('open', 'closed', 'handoff')),
    last_message_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, id, contact_id),
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict
);

create table public.messages (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    conversation_id uuid not null,
    contact_id uuid not null,
    external_message_id text,
    direction text not null check (direction in ('inbound', 'outbound')),
    role text not null check (role in ('user', 'assistant', 'system')),
    message_type text not null default 'text',
    text text,
    created_at timestamptz not null default now(),
    foreign key (clinic_id, conversation_id, contact_id)
        references public.conversations(clinic_id, id, contact_id) on delete cascade,
    foreign key (clinic_id, contact_id)
        references public.contacts(clinic_id, id) on delete restrict
);

create unique index messages_external_message_id_unique
    on public.messages(external_message_id)
    where external_message_id is not null;

create trigger contacts_set_updated_at
before update on public.contacts
for each row execute function public.set_updated_at();

create trigger patients_set_updated_at
before update on public.patients
for each row execute function public.set_updated_at();

create trigger conversations_set_updated_at
before update on public.conversations
for each row execute function public.set_updated_at();

commit;
