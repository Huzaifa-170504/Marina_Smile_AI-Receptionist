begin;

-- Google Calendar becomes the primary holiday source. Manual rows remain for
-- exceptions, while synchronized rows carry their source/event identifiers.
alter table public.clinic_holidays
    add column if not exists source text not null default 'manual',
    add column if not exists google_event_id text,
    add column if not exists calendar_id text,
    add column if not exists last_synced_at timestamptz,
    add column if not exists event_updated_at timestamptz;

do $$
begin
    if not exists (
        select 1 from pg_catalog.pg_constraint
        where conname = 'clinic_holidays_source_check'
    ) then
        alter table public.clinic_holidays
            add constraint clinic_holidays_source_check
            check (source in ('manual', 'google_calendar'));
    end if;
end;
$$;

create index if not exists clinic_holidays_source_calendar_idx
    on public.clinic_holidays (clinic_id, source, calendar_id);

create table if not exists public.clinic_holiday_sync (
    clinic_id uuid primary key references public.clinics(id) on delete cascade,
    calendar_id text,
    sync_token text,
    last_synced_at timestamptz,
    last_attempt_at timestamptz,
    locked_until timestamptz,
    last_error text,
    last_status text not null default 'never',
    coverage_end date,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists clinic_holiday_sync_set_updated_at on public.clinic_holiday_sync;
create trigger clinic_holiday_sync_set_updated_at
before update on public.clinic_holiday_sync
for each row execute function public.set_updated_at();

alter table public.clinic_holiday_sync enable row level security;

grant select on public.clinic_holiday_sync to authenticated;
grant select, insert, update, delete on public.clinic_holiday_sync to service_role;

drop policy if exists clinic_holiday_sync_clinic_select on public.clinic_holiday_sync;
create policy clinic_holiday_sync_clinic_select on public.clinic_holiday_sync
for select to authenticated
using (clinic_id = private.current_clinic_id());

drop policy if exists clinic_holiday_sync_owner_all on public.clinic_holiday_sync;
create policy clinic_holiday_sync_owner_all on public.clinic_holiday_sync
for all to authenticated
using (
    clinic_id = private.current_clinic_id()
    and private.current_staff_role() = 'owner'
)
with check (
    clinic_id = private.current_clinic_id()
    and private.current_staff_role() = 'owner'
);

-- Lease-based claim prevents concurrent schedulers from syncing the same
-- clinic at the same time. A crashed run releases after the lease expires.
create or replace function public.claim_holiday_sync(
    p_clinic_id uuid,
    p_lease_minutes integer default 10
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    claimed boolean := false;
begin
    if not private.is_service_role() then
        raise exception using message = 'Holiday sync claim requires the trusted server role', errcode = '42501';
    end if;
    if p_lease_minutes is null or p_lease_minutes < 1 or p_lease_minutes > 1440 then
        raise exception using message = 'Holiday sync lease must be between 1 and 1440 minutes', errcode = '22023';
    end if;

    insert into public.clinic_holiday_sync (clinic_id)
    values (p_clinic_id)
    on conflict (clinic_id) do nothing;

    update public.clinic_holiday_sync
       set locked_until = pg_catalog.now() + pg_catalog.make_interval(mins => p_lease_minutes),
           last_attempt_at = pg_catalog.now(),
           last_status = 'running'
     where clinic_id = p_clinic_id
       and (locked_until is null or locked_until < pg_catalog.now())
    returning true into claimed;

    return coalesce(claimed, false);
end;
$$;

revoke all on function public.claim_holiday_sync(uuid, integer)
    from public, anon, authenticated, service_role;
grant execute on function public.claim_holiday_sync(uuid, integer) to service_role;

comment on table public.clinic_holiday_sync is
    'Per-clinic Google holiday calendar sync state, lease, and last successful run.';

commit;
