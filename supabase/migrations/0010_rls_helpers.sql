begin;

create or replace function private.is_service_role()
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
    select coalesce((select auth.jwt() ->> 'role'), '') = 'service_role';
$$;

create or replace function private.current_staff_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
    select su.id
      from public.staff_users as su
     where su.auth_user_id = (select auth.uid())
       and su.active
     limit 1;
$$;

create or replace function private.current_clinic_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
    select su.clinic_id
      from public.staff_users as su
     where su.auth_user_id = (select auth.uid())
       and su.active
     limit 1;
$$;

create or replace function private.current_staff_role()
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select su.role
      from public.staff_users as su
     where su.auth_user_id = (select auth.uid())
       and su.active
     limit 1;
$$;

create or replace function private.current_doctor_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
    select su.doctor_id
      from public.staff_users as su
     where su.auth_user_id = (select auth.uid())
       and su.active
       and su.role = 'doctor'
     limit 1;
$$;

create or replace function private.has_permission(permission_text text)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    staff_id uuid;
    staff_role text;
    override_value boolean;
begin
    select su.id, su.role
      into staff_id, staff_role
      from public.staff_users as su
     where su.auth_user_id = (select auth.uid())
       and su.active
     limit 1;

    if staff_id is null then
        return false;
    end if;

    select spo.allowed
      into override_value
      from public.staff_permission_overrides as spo
     where spo.staff_user_id = staff_id
       and spo.permission = permission_text;

    if found then
        return override_value;
    end if;

    if staff_role = 'owner' then
        return true;
    end if;

    return case staff_role
        when 'doctor' then permission_text in (
            'appointment.view_own', 'appointment.reschedule_own', 'appointment.cancel_own',
            'queue.view_own', 'queue.update_own', 'schedule.manage_own',
            'patient.view_assigned'
        )
        when 'receptionist' then permission_text in (
            'appointment.view_all', 'appointment.create', 'appointment.reschedule_any',
            'appointment.cancel_any', 'queue.view_all', 'queue.update_all',
            'schedule.manage_any', 'patient.view_all'
        )
        when 'payment_verifier' then permission_text in (
            'payment.verify', 'payment.view_proof'
        )
        else false
    end;
end;
$$;

create or replace function private.can_view_patient(target_patient_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select
        private.has_permission('patient.view_all')
        or (
            private.has_permission('patient.view_assigned')
            and exists (
                select 1
                  from public.appointments as a
                 where a.clinic_id = private.current_clinic_id()
                   and a.patient_id = target_patient_id
                   and a.dentist_id = private.current_doctor_id()
            )
        )
        or (
            private.has_permission('payment.verify')
            and exists (
                select 1
                  from public.booking_requests as br
                 where br.clinic_id = private.current_clinic_id()
                   and br.patient_id = target_patient_id
                   and not br.payment_exempt
                   and br.status in ('payment_submitted', 'payment_rejected', 'payment_verified')
            )
        );
$$;

create or replace function private.can_view_contact(target_contact_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select
        private.has_permission('patient.view_all')
        or exists (
            select 1
              from public.patients as p
             where p.clinic_id = private.current_clinic_id()
               and p.primary_contact_id = target_contact_id
               and private.can_view_patient(p.id)
        );
$$;

revoke all on schema private from public;
grant usage on schema private to authenticated;

revoke all on function private.is_service_role() from public, anon, authenticated, service_role;
revoke all on function private.current_staff_id() from public;
revoke all on function private.current_clinic_id() from public;
revoke all on function private.current_staff_role() from public;
revoke all on function private.current_doctor_id() from public;
revoke all on function private.has_permission(text) from public;
revoke all on function private.can_view_patient(uuid) from public;
revoke all on function private.can_view_contact(uuid) from public;

grant execute on function private.current_staff_id() to authenticated;
grant execute on function private.current_clinic_id() to authenticated;
grant execute on function private.current_staff_role() to authenticated;
grant execute on function private.current_doctor_id() to authenticated;
grant execute on function private.has_permission(text) to authenticated;
grant execute on function private.can_view_patient(uuid) to authenticated;
grant execute on function private.can_view_contact(uuid) to authenticated;

commit;
