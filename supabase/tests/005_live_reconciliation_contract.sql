-- Catalog-level security contract after migrations 0001-0016.
begin;

create or replace function pg_temp.assert_true(condition boolean, message text)
returns void
language plpgsql
as $$
begin
    if not pg_catalog.coalesce(condition, false) then
        raise exception 'Assertion failed: %', message;
    end if;
end;
$$;

select pg_temp.assert_true(
    not has_function_privilege('anon', 'public.claim_outbound_notification()', 'EXECUTE'),
    'anon cannot claim outbound notifications'
);
select pg_temp.assert_true(
    not has_function_privilege('authenticated', 'public.claim_outbound_notification()', 'EXECUTE'),
    'authenticated cannot claim outbound notifications'
);
select pg_temp.assert_true(
    has_function_privilege('service_role', 'public.claim_outbound_notification()', 'EXECUTE'),
    'service role can claim outbound notifications'
);
select pg_temp.assert_true(
    not has_table_privilege('authenticated', 'public.appointments', 'TRUNCATE')
    and not has_table_privilege('authenticated', 'public.appointments', 'REFERENCES')
    and not has_table_privilege('authenticated', 'public.appointments', 'TRIGGER'),
    'authenticated has no broad structural table privileges'
);
select pg_temp.assert_true(
    (select proconfig @> array['search_path=""']
       from pg_proc
      where oid = 'public.claim_outbound_notification()'::regprocedure),
    'notification claimant has an empty pinned search_path'
);
select pg_temp.assert_true(
    (select relreplident = 'f'
       from pg_class
      where oid = 'public.appointments'::regclass),
    'appointments uses full replica identity'
);
select pg_temp.assert_true(
    (select relreplident = 'f'
       from pg_class
      where oid = 'public.schedule_overrides'::regclass),
    'schedule overrides use full replica identity'
);
select pg_temp.assert_true(
    exists (
        select 1 from pg_policies
         where schemaname = 'public'
           and tablename = 'doctor_status_change_requests'
           and policyname = 'doctor_requests_doctor_insert'
    ),
    'doctor status requests have an authenticated insert policy'
);
select pg_temp.assert_true(
    exists (
        select 1 from pg_policies
         where schemaname = 'public'
           and tablename = 'conversations'
           and policyname = 'conversations_authorized_select'
    ),
    'conversations have relationship-aware read access'
);
select pg_temp.assert_true(
    exists (
        select 1 from pg_policies
         where schemaname = 'storage'
           and tablename = 'objects'
           and policyname = 'payment_proofs_authorized_update'
           and qual like '%foldername%current_clinic_id%'
    ),
    'payment proof updates are clinic-folder scoped'
);
select pg_temp.assert_true(
    (select not public from storage.buckets where id = 'payment-proofs')
    and (select not public from storage.buckets where id = 'clinic-knowledge'),
    'existing storage buckets remain private'
);
select pg_temp.assert_true(
    to_regclass('public.appointments_no_active_overlap') is not null,
    'appointment overlap exclusion support remains intact'
);

rollback;
