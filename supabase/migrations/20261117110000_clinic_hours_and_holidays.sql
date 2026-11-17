begin;

-- Configurable clinic operating hours. All booking interfaces (Telegram,
-- scripts, rescheduling, dashboard-backed operations) share these rules.
alter table public.clinics
    add column if not exists open_time time not null default '10:00',
    add column if not exists close_time time not null default '18:00',
    add column if not exists break_start time default '13:00',
    add column if not exists break_end time default '14:00',
    add column if not exists closed_weekdays smallint[] not null default '{6,7}';

do $$
begin
    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'clinics_operating_hours_check'
    ) then
        alter table public.clinics
            add constraint clinics_operating_hours_check
            check (
                close_time > open_time
                and (
                    (break_start is null and break_end is null)
                    or (
                        break_start is not null
                        and break_end is not null
                        and break_start > open_time
                        and break_end < close_time
                        and break_end > break_start
                    )
                )
            );
    end if;

    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'clinics_closed_weekdays_check'
    ) then
        alter table public.clinics
            add constraint clinics_closed_weekdays_check
            check (
                closed_weekdays <@ array[1,2,3,4,5,6,7]::smallint[]
            );
    end if;
end;
$$;

comment on column public.clinics.open_time is 'Clinic opening time in the clinic timezone.';
comment on column public.clinics.close_time is 'Clinic closing time in the clinic timezone.';
comment on column public.clinics.break_start is 'Daily break start, or null when there is no break.';
comment on column public.clinics.break_end is 'Daily break end, or null when there is no break.';
comment on column public.clinics.closed_weekdays is 'ISO weekdays (1=Monday, 7=Sunday) when the clinic is closed.';

-- Configurable public holiday calendar. Owners add/update dates without code
-- changes; the table supports any year and dates that change annually.
create table if not exists public.clinic_holidays (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete cascade,
    holiday_date date not null,
    name text,
    created_at timestamptz not null default now(),
    unique (clinic_id, holiday_date)
);

create index if not exists clinic_holidays_clinic_date_idx
    on public.clinic_holidays (clinic_id, holiday_date);

alter table public.clinic_holidays enable row level security;

grant select on public.clinic_holidays to authenticated;
grant select, insert, update, delete on public.clinic_holidays to service_role;

drop policy if exists clinic_holidays_clinic_select on public.clinic_holidays;
create policy clinic_holidays_clinic_select on public.clinic_holidays
for select to authenticated
using (clinic_id = private.current_clinic_id());

drop policy if exists clinic_holidays_owner_all on public.clinic_holidays;
create policy clinic_holidays_owner_all on public.clinic_holidays
for all to authenticated
using (
    clinic_id = private.current_clinic_id()
    and private.current_staff_role() = 'owner'
)
with check (
    clinic_id = private.current_clinic_id()
    and private.current_staff_role() = 'owner'
);

-- Authoritative final validation for every appointment insert/update,
-- regardless of which interface created it.
create or replace function private.validate_appointment_clinic_hours()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    clinic_row public.clinics%rowtype;
    local_start timestamp;
    local_end timestamp;
    local_date date;
begin
    if new.status not in ('pending', 'confirmed') then
        return new;
    end if;

    select * into clinic_row
      from public.clinics
     where id = new.clinic_id
       and active
     for share;
    if not found then
        raise exception using message = 'Active clinic not found', errcode = 'P0002';
    end if;

    local_start := pg_catalog.timezone(clinic_row.timezone, new.start_at);
    local_end := pg_catalog.timezone(clinic_row.timezone, new.end_at);
    local_date := local_start::date;

    if local_end::date <> local_date then
        raise exception using
            message = 'Appointments must be within clinic hours (10:00 AM–6:00 PM).',
            errcode = 'check_violation';
    end if;

    if extract(isodow from local_date)::smallint = any(clinic_row.closed_weekdays) then
        raise exception using
            message = 'The clinic is closed on Saturdays and Sundays. Please select a weekday between Monday and Friday.',
            errcode = 'check_violation';
    end if;

    if exists (
        select 1
          from public.clinic_holidays
         where clinic_id = new.clinic_id
           and holiday_date = local_date
    ) then
        raise exception using
            message = 'The clinic is closed on this public holiday. Please select another available date.',
            errcode = 'check_violation';
    end if;

    if local_start::time < clinic_row.open_time
       or local_end::time > clinic_row.close_time then
        raise exception using
            message = 'Appointments must be within clinic hours (10:00 AM–6:00 PM).',
            errcode = 'check_violation';
    end if;

    if clinic_row.break_start is not null
       and clinic_row.break_end is not null
       and local_start::time < clinic_row.break_end
       and local_end::time > clinic_row.break_start then
        raise exception using
            message = 'Appointments cannot overlap the clinic break between 1:00 PM and 2:00 PM.',
            errcode = 'check_violation';
    end if;

    return new;
end;
$$;

revoke all on function private.validate_appointment_clinic_hours()
    from public, anon, authenticated, service_role;

drop trigger if exists appointments_validate_clinic_hours on public.appointments;
create trigger appointments_validate_clinic_hours
before insert or update of start_at, end_at, status on public.appointments
for each row execute function private.validate_appointment_clinic_hours();

commit;
