begin;

create table public.demo_query_events (
    id bigint generated always as identity primary key,
    clinic_id uuid not null references public.clinics(id) on delete cascade,
    fingerprint_hash text not null,
    query_type text not null check (query_type in ('knowledge', 'availability')),
    created_at timestamptz not null default pg_catalog.now(),
    constraint demo_query_events_fingerprint_hash_check
        check (fingerprint_hash ~ '^[0-9a-f]{64}$')
);

create index demo_query_events_quota_lookup_idx
    on public.demo_query_events(clinic_id, fingerprint_hash, created_at desc);

alter table public.demo_query_events enable row level security;
revoke all on table public.demo_query_events from public, anon, authenticated;
grant select, insert, delete on table public.demo_query_events to service_role;
grant usage, select on sequence public.demo_query_events_id_seq to service_role;

create or replace function public.consume_demo_query_quota(
    p_clinic_id uuid,
    p_fingerprint_hash text,
    p_query_type text,
    p_limit integer,
    p_window_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    recent_count integer;
begin
    if not private.is_service_role() then
        raise exception using message = 'Demo quota requires the trusted server role', errcode = '42501';
    end if;
    if p_clinic_id is null
       or p_fingerprint_hash !~ '^[0-9a-f]{64}$'
       or p_query_type not in ('knowledge', 'availability')
       or p_limit not between 1 and 100
       or p_window_seconds not between 60 and 86400 then
        raise exception using message = 'Invalid demo quota parameters', errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_clinic_id::text || ':' || p_fingerprint_hash, 0)
    );

    select pg_catalog.count(*) into recent_count
      from public.demo_query_events
     where clinic_id = p_clinic_id
       and fingerprint_hash = p_fingerprint_hash
       and created_at >= pg_catalog.now() - pg_catalog.make_interval(secs => p_window_seconds);

    if recent_count >= p_limit then
        return false;
    end if;

    insert into public.demo_query_events (clinic_id, fingerprint_hash, query_type)
    values (p_clinic_id, p_fingerprint_hash, p_query_type);

    delete from public.demo_query_events
     where created_at < pg_catalog.now() - interval '7 days';

    return true;
end;
$$;

revoke all on function public.consume_demo_query_quota(uuid, text, text, integer, integer)
    from public, anon, authenticated;
grant execute on function public.consume_demo_query_quota(uuid, text, text, integer, integer)
    to service_role;

commit;
