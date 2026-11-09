begin;

-- The appointment audit trigger is SECURITY INVOKER and calls private schema
-- helpers. Direct appointment updates through the service role therefore fail
-- with "permission denied for schema private", which blocks patient
-- cancellation and calendar-event compensation.
--
-- Make the trigger function SECURITY DEFINER so it can read the actor helpers
-- while retaining an empty search path.
create or replace function private.audit_appointment_write()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    audit_action text;
    actor_role text := coalesce(private.current_staff_role(), 'system');
    actor_staff_id uuid := private.current_staff_id();
begin
    if tg_op = 'INSERT' then
        audit_action := 'created';
    elsif new.status is distinct from old.status then
        audit_action := new.status;
    elsif new.dentist_id is distinct from old.dentist_id then
        audit_action := 'doctor_changed';
    elsif new.start_at is distinct from old.start_at or new.end_at is distinct from old.end_at then
        audit_action := 'rescheduled';
    else
        audit_action := 'updated';
    end if;

    insert into public.appointment_audit_log (
        clinic_id, appointment_id, patient_id, actor_type, actor_id,
        action, old_values, new_values
    ) values (
        new.clinic_id, new.id, new.patient_id, actor_role, actor_staff_id,
        audit_action,
        case when tg_op = 'UPDATE' then pg_catalog.jsonb_build_object(
            'status', old.status,
            'dentist_id', old.dentist_id,
            'start_at', old.start_at,
            'end_at', old.end_at,
            'google_calendar_event_id', old.google_calendar_event_id
        ) end,
        pg_catalog.jsonb_build_object(
            'status', new.status,
            'dentist_id', new.dentist_id,
            'start_at', new.start_at,
            'end_at', new.end_at,
            'google_calendar_event_id', new.google_calendar_event_id
        )
    );
    return new;
end;
$$;

revoke all on function private.audit_appointment_write()
    from public, anon, authenticated, service_role;

commit;
