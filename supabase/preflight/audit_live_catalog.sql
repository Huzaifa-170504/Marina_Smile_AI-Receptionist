-- Read-only live catalog audit for the Marina Smile Supabase project.
-- Safe to run at any time: every statement is a SELECT against system catalogs
-- or aggregate counts. It performs no DDL, no DML, and no migration repair.
--
-- The PostgREST API exposes tables and RPC names but not constraints, indexes,
-- triggers, policy bodies, or grants. Run this in the Supabase SQL editor when
-- you need to prove the live catalog matches the migration intent.
--
-- Review each result against supabase/migrations/*.sql. Any missing row is
-- documentation drift to reconcile with a NEW additive migration; never edit an
-- applied one.

-- 1. Tables, RLS state, and approximate size.
select n.nspname as schema_name,
       c.relname as table_name,
       c.relrowsecurity as rls_enabled,
       c.relforcerowsecurity as rls_forced,
       pg_catalog.pg_get_userbyid(c.relowner) as owner
  from pg_catalog.pg_class c
  join pg_catalog.pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public'
   and c.relkind in ('r', 'p')
 order by c.relname;

-- 2. Columns that matter for identity, scheduling, and privacy.
select table_name, column_name, data_type, is_nullable, column_default
  from information_schema.columns
 where table_schema = 'public'
   and (table_name, column_name) in (
       ('appointments', 'patient_id'),
       ('appointments', 'booking_request_id'),
       ('appointments', 'idempotency_key'),
       ('appointments', 'google_calendar_event_id'),
       ('appointment_change_requests', 'old_service_id'),
       ('appointment_change_requests', 'old_google_calendar_event_id'),
       ('contacts', 'whatsapp_id'),
       ('contacts', 'phone_number'),
       ('contacts', 'email'),
       ('contacts', 'telegram_chat_id'),
       ('outbound_notifications', 'channel'),
       ('clinics', 'open_time'),
       ('clinics', 'close_time'),
       ('clinics', 'break_start'),
       ('clinics', 'break_end'),
       ('clinics', 'closed_weekdays')
   )
 order by table_name, column_name;

-- 3. Constraints, including NOT NULL and foreign keys.
select t.relname as table_name,
       c.conname as constraint_name,
       c.contype as constraint_type,
       pg_catalog.pg_get_constraintdef(c.oid) as definition
  from pg_catalog.pg_constraint c
  join pg_catalog.pg_class t on t.oid = c.conrelid
  join pg_catalog.pg_namespace n on n.oid = t.relnamespace
 where n.nspname = 'public'
 order by t.relname, c.contype, c.conname;

-- 4. Indexes (exclusion constraints and unique guards are the concurrency
--    boundary; a missing appointment overlap constraint is critical).
select tablename, indexname, indexdef
  from pg_catalog.pg_indexes
 where schemaname = 'public'
 order by tablename, indexname;

-- 5. RLS policies with their full USING / WITH CHECK expressions.
select schemaname, tablename, policyname, permissive, roles, cmd,
       qual, with_check
  from pg_catalog.pg_policies
 where schemaname = 'public'
 order by tablename, policyname;

-- 6. Table and function grants that could expose data to anon/authenticated.
select table_schema, table_name, grantee, privilege_type
  from information_schema.role_table_grants
 where table_schema = 'public'
   and grantee in ('anon', 'authenticated', 'service_role')
 order by table_name, grantee, privilege_type;

select p.proname as function_name, r.rolname as grantee, p.prosecdef as security_definer
  from pg_catalog.pg_proc p
  join pg_catalog.pg_namespace n on n.oid = p.pronamespace
  cross join lateral aclexplode(p.proacl) a
  join pg_catalog.pg_roles r on r.oid = a.grantee
 where n.nspname = 'public'
   and r.rolname in ('anon', 'authenticated', 'service_role')
 order by p.proname, r.rolname;

-- 7. SECURITY DEFINER functions without a pinned search_path.
select n.nspname as schema_name, p.proname as function_name,
       p.proconfig as function_settings
  from pg_catalog.pg_proc p
  join pg_catalog.pg_namespace n on n.oid = p.pronamespace
 where n.nspname in ('public', 'private')
   and p.prosecdef
   and (p.proconfig is null
        or not exists (
            select 1 from unnest(p.proconfig) setting
             where setting like 'search_path=%'
        ))
 order by n.nspname, p.proname;

-- 8. Triggers, including append-only audit and clinic-hours validation.
select event_object_table as table_name,
       trigger_name,
       event_manipulation,
       action_timing,
       action_statement
  from information_schema.triggers
 where trigger_schema = 'public'
 order by event_object_table, trigger_name;

-- 9. Data integrity checks (aggregate, no identifiers returned).
select 'active_overlapping_appointments' as check_name, count(*) as violations
  from public.appointments a
  join public.appointments b
    on a.id < b.id
   and a.clinic_id = b.clinic_id
   and a.dentist_id = b.dentist_id
   and a.status in ('pending', 'confirmed')
   and b.status in ('pending', 'confirmed')
   and a.start_at < b.end_at
   and b.start_at < a.end_at
union all
select 'confirmed_without_calendar_event', count(*)
  from public.appointments
 where status = 'confirmed' and google_calendar_event_id is null
union all
select 'anonymized_contacts_with_identifiers', count(*)
  from public.contacts
 where name = 'Deleted Contact'
   and (phone_number is not null or email is not null or telegram_chat_id is not null)
union all
select 'anonymized_contacts_with_live_whatsapp_id', count(*)
  from public.contacts
 where name = 'Deleted Contact'
   and whatsapp_id is not null
   and whatsapp_id not like 'deleted:%'
union all
select 'duplicate_google_holiday_dates', count(*)
  from (
      select clinic_id, holiday_date
        from public.clinic_holidays
       where source = 'google_calendar'
       group by clinic_id, holiday_date
      having count(*) > 1
  ) duplicates
union all
select 'overdue_pending_notifications', count(*)
  from public.outbound_notifications
 where status = 'pending'
   and scheduled_at < pg_catalog.now() - interval '1 hour';

-- 10. Holiday sync health. last_status='error' with
--     'Google Calendar credentials are unavailable' means the deployed
--     service-account secret file is missing or unreadable; existing holidays
--     are still valid because a failed sync never deletes rows.
select clinic_id, last_status, last_attempt_at, last_synced_at,
       coverage_end, sync_token is not null as has_sync_token, last_error
  from public.clinic_holiday_sync
 order by clinic_id;
