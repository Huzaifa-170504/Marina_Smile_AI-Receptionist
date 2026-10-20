begin;

create table public.schedule_overrides (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    dentist_id uuid not null,
    start_at timestamptz not null,
    end_at timestamptz not null,
    override_type text not null check (override_type in ('unavailable', 'extra_availability')),
    reason text,
    created_by_staff_id uuid not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (clinic_id, dentist_id)
        references public.dentists(clinic_id, id) on delete restrict,
    foreign key (clinic_id, created_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check (end_at > start_at)
);

create table public.appointment_visits (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    appointment_id uuid not null unique,
    queue_status text not null default 'scheduled' check (
        queue_status in ('scheduled', 'checked_in', 'waiting', 'in_progress', 'completed', 'no_show')
    ),
    checked_in_at timestamptz,
    waiting_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    no_show_at timestamptz,
    updated_by_staff_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    foreign key (clinic_id, appointment_id)
        references public.appointments(clinic_id, id) on delete restrict,
    foreign key (clinic_id, updated_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict
);

create or replace function private.validate_queue_transition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.queue_status <> 'scheduled' then
            raise exception using message = 'A visit must start as scheduled', errcode = 'check_violation';
        end if;
        if not exists (
            select 1
              from public.appointments as a
             where a.id = new.appointment_id
               and a.clinic_id = new.clinic_id
               and a.status = 'confirmed'
        ) then
            raise exception using message = 'A visit requires a confirmed appointment', errcode = 'check_violation';
        end if;
    end if;

    if tg_op = 'UPDATE' and new.queue_status is distinct from old.queue_status then
        if not (
            (old.queue_status = 'scheduled' and new.queue_status in ('checked_in', 'no_show'))
            or (old.queue_status = 'checked_in' and new.queue_status in ('waiting', 'no_show'))
            or (old.queue_status = 'waiting' and new.queue_status in ('in_progress', 'no_show'))
            or (old.queue_status = 'in_progress' and new.queue_status = 'completed')
        ) then
            raise exception using
                message = format('Invalid queue transition: %s -> %s', old.queue_status, new.queue_status),
                errcode = 'check_violation';
        end if;
    end if;

    if new.queue_status = 'checked_in' and new.checked_in_at is null then new.checked_in_at := pg_catalog.now(); end if;
    if new.queue_status = 'waiting' and new.waiting_at is null then new.waiting_at := pg_catalog.now(); end if;
    if new.queue_status = 'in_progress' and new.started_at is null then new.started_at := pg_catalog.now(); end if;
    if new.queue_status = 'completed' and new.completed_at is null then new.completed_at := pg_catalog.now(); end if;
    if new.queue_status = 'no_show' and new.no_show_at is null then new.no_show_at := pg_catalog.now(); end if;
    return new;
end;
$$;

create or replace function private.create_visit_for_confirmed_appointment()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.status = 'confirmed' and (tg_op = 'INSERT' or old.status is distinct from 'confirmed') then
        insert into public.appointment_visits (clinic_id, appointment_id)
        values (new.clinic_id, new.id)
        on conflict (appointment_id) do nothing;
    end if;
    return new;
end;
$$;

create trigger appointment_visits_validate_transition
before insert or update on public.appointment_visits
for each row execute function private.validate_queue_transition();

create trigger appointment_visits_set_updated_at
before update on public.appointment_visits
for each row execute function public.set_updated_at();

create trigger appointments_create_visit
after insert or update of status on public.appointments
for each row execute function private.create_visit_for_confirmed_appointment();

create trigger schedule_overrides_set_updated_at
before update on public.schedule_overrides
for each row execute function public.set_updated_at();

commit;
