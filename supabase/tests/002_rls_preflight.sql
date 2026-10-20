-- Structural RLS preflight. Role/JWT behavior is documented in docs/database/rls.md.
begin;

create or replace function pg_temp.assert_true(condition boolean, message text)
returns void
language plpgsql
as $$
begin
    if not coalesce(condition, false) then
        raise exception 'Assertion failed: %', message;
    end if;
end;
$$;

with expected(table_name) as (
    select unnest(array[
        'clinics', 'staff_users', 'staff_permission_overrides', 'dentists',
        'doctor_status_change_requests', 'services', 'dentist_services',
        'dentist_schedules', 'contacts', 'patients', 'conversations', 'messages',
        'booking_requests', 'payments', 'appointments', 'schedule_overrides',
        'appointment_visits', 'appointment_change_requests',
        'appointment_change_options', 'appointment_audit_log',
        'reminder_consents', 'outbound_notifications', 'refund_requests', 'handoffs',
        'tool_executions', 'knowledge_documents', 'knowledge_chunks'
    ]::text[])
), missing as (
    select e.table_name
      from expected e
      left join pg_class c on c.relname = e.table_name
      left join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
     where c.oid is null or not c.relrowsecurity
)
select pg_temp.assert_true(not exists (select 1 from missing), 'every public application table must have RLS enabled');

select pg_temp.assert_true(
    not has_schema_privilege('anon', 'private', 'usage'),
    'anon must not have private schema usage'
);

select pg_temp.assert_true(
    not has_schema_privilege('anon', 'public', 'usage'),
    'anon must not have public Data API schema usage'
);

select pg_temp.assert_true(
    not has_function_privilege('public', 'private.current_clinic_id()', 'execute'),
    'private helper execution must not be granted to PUBLIC'
);

select pg_temp.assert_true(
    (select not public from storage.buckets where id = 'payment-proofs'),
    'payment-proofs bucket must be private'
);

select pg_temp.assert_true(
    (select not public from storage.buckets where id = 'clinic-knowledge'),
    'clinic-knowledge bucket must be private'
);

select pg_temp.assert_true(
    (select not public from storage.buckets where id = 'refund-proofs'),
    'refund-proofs bucket must be private'
);

select pg_temp.assert_true(
    (select count(*) > 0 from pg_policies where schemaname = 'public'),
    'public RLS policies must exist'
);

select pg_temp.assert_true(
    not has_function_privilege(
        'authenticated',
        'public.reserve_appointment(uuid,uuid,uuid,uuid,timestamptz,timestamptz,uuid)',
        'execute'
    ),
    'authenticated users must not execute the server reservation RPC'
);

select pg_temp.assert_true(
    not has_function_privilege(
        'authenticated',
        'public.confirm_appointment_reservation(uuid,uuid,text)',
        'execute'
    ),
    'authenticated users must not execute the server confirmation RPC'
);

select pg_temp.assert_true(
    has_function_privilege(
        'service_role',
        'public.reserve_appointment(uuid,uuid,uuid,uuid,timestamptz,timestamptz,uuid)',
        'execute'
    ),
    'service_role must execute the server reservation RPC'
);

select pg_temp.assert_true(
    not has_table_privilege('authenticated', 'public.appointments', 'insert')
    and not has_table_privilege('authenticated', 'public.appointments', 'update'),
    'authenticated appointment table access must be read-only'
);

select pg_temp.assert_true(
    not has_table_privilege('authenticated', 'public.payments', 'insert')
    and not has_table_privilege('authenticated', 'public.payments', 'update'),
    'authenticated users must not mutate payments directly'
);

select pg_temp.assert_true(
    has_table_privilege('authenticated', 'public.refund_requests', 'select')
    and not has_table_privilege('authenticated', 'public.refund_requests', 'insert')
    and not has_table_privilege('authenticated', 'public.refund_requests', 'update')
    and not has_table_privilege('authenticated', 'public.refund_requests', 'delete'),
    'authenticated refund access must be RLS-scoped and read-only outside RPCs'
);

select pg_temp.assert_true(
    not has_function_privilege(
        'authenticated',
        'public.request_refund(uuid,uuid,text,numeric,uuid)',
        'execute'
    )
    and has_function_privilege(
        'service_role',
        'public.request_refund(uuid,uuid,text,numeric,uuid)',
        'execute'
    ),
    'patient refund requests must be trusted-server only'
);

select pg_temp.assert_true(
    not has_function_privilege(
        'authenticated',
        'public.check_in_appointment(uuid,uuid,uuid,integer,integer)',
        'execute'
    )
    and has_function_privilege(
        'service_role',
        'public.check_in_appointment(uuid,uuid,uuid,integer,integer)',
        'execute'
    ),
    'check-in must be trusted-server only'
);

select pg_temp.assert_true(
    to_regprocedure('public.transition_queue_state(uuid,text,text,uuid)') is null
    and has_function_privilege(
        'authenticated',
        'public.transition_queue_state(uuid,text,text)',
        'execute'
    ),
    'legacy caller-supplied queue actor RPC must be removed'
);

select pg_temp.assert_true(
    not has_column_privilege('authenticated', 'public.booking_requests', 'payment_exempt', 'update')
    and not has_column_privilege('authenticated', 'public.booking_requests', 'patient_id', 'update'),
    'protected booking fields must not be directly mutable'
);

select pg_temp.assert_true(
    not has_table_privilege('service_role', 'public.appointment_audit_log', 'update')
    and not has_table_privilege('service_role', 'public.appointment_audit_log', 'delete'),
    'audit access must be append-only'
);

select pg_temp.assert_true(
    not exists (
        select 1
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
         where n.nspname in ('public', 'private')
           and p.prosecdef
           and not pg_catalog.coalesce(
               pg_catalog.array_to_string(p.proconfig, ',') ~ '(^|,)search_path=',
               false
           )
    ),
    'security-definer functions must use a fixed search_path'
);

rollback;
