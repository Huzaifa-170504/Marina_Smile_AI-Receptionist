begin;

-- Each selected service can have its own doctor/time inside one booking and one
-- appointment (the appointment spans the earliest start to the latest end).
alter table public.booking_request_services
    add column if not exists dentist_id uuid,
    add column if not exists start_at timestamptz,
    add column if not exists end_at timestamptz;

alter table public.appointment_services
    add column if not exists dentist_id uuid,
    add column if not exists start_at timestamptz,
    add column if not exists end_at timestamptz;

do $$
begin
    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'booking_request_services_dentist_fkey'
    ) then
        alter table public.booking_request_services
            add constraint booking_request_services_dentist_fkey
            foreign key (clinic_id, dentist_id)
            references public.dentists(clinic_id, id) on delete restrict;
    end if;

    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'booking_request_services_schedule_check'
    ) then
        alter table public.booking_request_services
            add constraint booking_request_services_schedule_check
            check (
                (start_at is null and end_at is null)
                or (start_at is not null and end_at is not null and end_at > start_at)
            );
    end if;

    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'appointment_services_dentist_fkey'
    ) then
        alter table public.appointment_services
            add constraint appointment_services_dentist_fkey
            foreign key (clinic_id, dentist_id)
            references public.dentists(clinic_id, id) on delete restrict;
    end if;

    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'appointment_services_schedule_check'
    ) then
        alter table public.appointment_services
            add constraint appointment_services_schedule_check
            check (
                (start_at is null and end_at is null)
                or (start_at is not null and end_at is not null and end_at > start_at)
            );
    end if;
end;
$$;

create index if not exists booking_request_services_dentist_idx
    on public.booking_request_services(clinic_id, dentist_id)
    where dentist_id is not null;

create index if not exists appointment_services_dentist_idx
    on public.appointment_services(clinic_id, dentist_id)
    where dentist_id is not null;

commit;
