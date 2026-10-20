begin;

-- Reconcile the verified legacy/live catalog with the canonical 0001-0014
-- contract. This migration is intentionally data-preserving: it does not
-- recreate application tables, reset data, or alter stable identifiers.

do $$
declare
    required_table text;
begin
    foreach required_table in array array[
        'clinics', 'staff_users', 'staff_permission_overrides', 'dentists',
        'doctor_status_change_requests', 'services', 'dentist_services',
        'dentist_schedules', 'contacts', 'patients', 'conversations', 'messages',
        'booking_requests', 'payments', 'appointments', 'schedule_overrides',
        'appointment_visits', 'appointment_change_requests',
        'appointment_change_options', 'appointment_audit_log',
        'reminder_consents', 'outbound_notifications', 'handoffs',
        'tool_executions', 'knowledge_documents', 'knowledge_chunks'
    ] loop
        if to_regclass(format('public.%I', required_table)) is null then
            raise exception 'Live reconciliation prerequisite is missing: public.%', required_table;
        end if;
    end loop;
end
$$;

create schema if not exists private;

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

create or replace function private.has_permission(p_permission text)
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
       and spo.permission = p_permission;

    if found then
        return override_value;
    end if;

    if staff_role = 'owner' then
        return true;
    end if;

    return case staff_role
        when 'doctor' then p_permission in (
            'appointment.view_own', 'appointment.reschedule_own', 'appointment.cancel_own',
            'queue.view_own', 'queue.update_own', 'schedule.manage_own',
            'patient.view_assigned'
        )
        when 'receptionist' then p_permission in (
            'appointment.view_all', 'appointment.create', 'appointment.reschedule_any',
            'appointment.cancel_any', 'queue.view_all', 'queue.update_all',
            'schedule.manage_any', 'patient.view_all'
        )
        when 'payment_verifier' then p_permission in (
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
revoke all on function private.current_staff_id() from public, anon, authenticated, service_role;
revoke all on function private.current_clinic_id() from public, anon, authenticated, service_role;
revoke all on function private.current_staff_role() from public, anon, authenticated, service_role;
revoke all on function private.current_doctor_id() from public, anon, authenticated, service_role;
revoke all on function private.has_permission(text) from public, anon, authenticated, service_role;
revoke all on function private.can_view_patient(uuid) from public, anon, authenticated, service_role;
revoke all on function private.can_view_contact(uuid) from public, anon, authenticated, service_role;

grant execute on function private.current_staff_id() to authenticated;
grant execute on function private.current_clinic_id() to authenticated;
grant execute on function private.current_staff_role() to authenticated;
grant execute on function private.current_doctor_id() to authenticated;
grant execute on function private.has_permission(text) to authenticated;
grant execute on function private.can_view_patient(uuid) to authenticated;
grant execute on function private.can_view_contact(uuid) to authenticated;

-- Remove the verified legacy callable surface. These overloads are retained
-- only for catalog compatibility and cannot be invoked through the Data API.
do $$
declare
    legacy_signature text;
begin
    foreach legacy_signature in array array[
        'public.claim_next_notification()',
        'public.deactivate_doctor(uuid,text)',
        'public.set_payment_exemption(uuid,text)',
        'public.transition_visit_status(uuid,text)',
        'public.verify_payment(uuid,numeric,text)'
    ] loop
        if to_regprocedure(legacy_signature) is not null then
            execute format(
                'revoke all on function %s from public, anon, authenticated, service_role',
                legacy_signature
            );
        end if;
    end loop;
end
$$;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = pg_catalog.now();
    return new;
end;
$$;

revoke all on function public.set_updated_at() from public, anon, authenticated, service_role;

-- Existing canonical lifecycle trigger functions are not exposed as RPCs.
-- Pin their resolution path without rewriting their validated business logic.
do $$
declare
    trigger_signature text;
begin
    foreach trigger_signature in array array[
        'private.validate_booking_request_transition()',
        'private.validate_payment_transition()',
        'private.validate_notification_transition()',
        'private.validate_doctor_status_request()',
        'private.validate_appointment_change_request()',
        'private.validate_dentist_deactivation()',
        'private.prevent_audit_log_mutation()',
        'private.audit_appointment_write()'
    ] loop
        if to_regprocedure(trigger_signature) is not null then
            execute format('alter function %s set search_path = ''''', trigger_signature);
            execute format(
                'revoke all on function %s from public, anon, authenticated, service_role',
                trigger_signature
            );
        end if;
    end loop;
end
$$;

-- Re-establish an opt-in Data API privilege model. In particular,
-- authenticated never receives TRUNCATE, REFERENCES, or TRIGGER.
revoke all on schema public from public, anon;
revoke all on all tables in schema public from public, anon, authenticated, service_role;
grant usage on schema public to authenticated, service_role;

alter default privileges for role postgres in schema public
    revoke all on tables from public, anon, authenticated;
alter default privileges for role postgres in schema public
    revoke execute on functions from public, anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on sequences from public, anon, authenticated;

grant select on table
    public.clinics, public.staff_users, public.staff_permission_overrides,
    public.dentists, public.doctor_status_change_requests, public.services,
    public.dentist_services, public.dentist_schedules, public.contacts,
    public.patients, public.conversations, public.messages,
    public.booking_requests, public.payments, public.appointments,
    public.schedule_overrides, public.appointment_visits,
    public.appointment_change_requests, public.appointment_change_options,
    public.appointment_audit_log, public.reminder_consents,
    public.outbound_notifications, public.handoffs, public.tool_executions,
    public.knowledge_documents, public.knowledge_chunks
to authenticated;

grant update on public.clinics to authenticated;
grant insert, update on public.staff_users,
    public.doctor_status_change_requests, public.services,
    public.contacts, public.patients, public.conversations,
    public.appointment_change_requests, public.reminder_consents,
    public.handoffs, public.knowledge_documents
to authenticated;
grant insert on public.dentists to authenticated;
grant update (name, specialty, bio) on public.dentists to authenticated;
grant insert, update, delete on public.staff_permission_overrides,
    public.dentist_services, public.dentist_schedules,
    public.schedule_overrides, public.appointment_change_options,
    public.knowledge_chunks
to authenticated;
grant insert on public.messages, public.booking_requests to authenticated;
grant update (
    preferred_dentist_id, preferred_date, preferred_window_start,
    preferred_window_end, reminder_count, last_reminder_at, expires_at
) on public.booking_requests to authenticated;

grant select, insert, update on table
    public.clinics, public.staff_users, public.staff_permission_overrides,
    public.dentists, public.doctor_status_change_requests, public.services,
    public.dentist_services, public.dentist_schedules, public.contacts,
    public.patients, public.conversations, public.messages,
    public.booking_requests, public.payments, public.appointments,
    public.schedule_overrides, public.appointment_visits,
    public.appointment_change_requests, public.appointment_change_options,
    public.reminder_consents, public.outbound_notifications,
    public.handoffs, public.tool_executions, public.knowledge_documents,
    public.knowledge_chunks
to service_role;
grant delete on public.staff_permission_overrides, public.dentist_services,
    public.dentist_schedules, public.schedule_overrides,
    public.appointment_change_options, public.outbound_notifications,
    public.knowledge_chunks, public.knowledge_documents
to service_role;
grant select, insert on public.appointment_audit_log to service_role;

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'clinics', 'staff_users', 'staff_permission_overrides', 'dentists',
        'doctor_status_change_requests', 'services', 'dentist_services',
        'dentist_schedules', 'contacts', 'patients', 'conversations', 'messages',
        'booking_requests', 'payments', 'appointments', 'schedule_overrides',
        'appointment_visits', 'appointment_change_requests',
        'appointment_change_options', 'appointment_audit_log',
        'reminder_consents', 'outbound_notifications', 'handoffs',
        'tool_executions', 'knowledge_documents', 'knowledge_chunks'
    ] loop
        execute format('alter table public.%I enable row level security', table_name);
    end loop;
end
$$;

-- Owner supervision is explicit and clinic-scoped for every operational
-- table; credentials remain exclusively in Supabase Auth.
do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'staff_users', 'staff_permission_overrides', 'dentists',
        'doctor_status_change_requests', 'services', 'dentist_services',
        'dentist_schedules', 'contacts', 'patients', 'conversations', 'messages',
        'booking_requests', 'payments', 'appointments', 'schedule_overrides',
        'appointment_visits', 'appointment_change_requests',
        'appointment_change_options', 'reminder_consents',
        'outbound_notifications', 'handoffs', 'tool_executions',
        'knowledge_documents', 'knowledge_chunks'
    ] loop
        execute format('drop policy if exists %I on public.%I', table_name || '_owner_all', table_name);
        execute format(
            'create policy %I on public.%I for all to authenticated '
            || 'using (clinic_id = private.current_clinic_id() and private.current_staff_role() = ''owner'') '
            || 'with check (clinic_id = private.current_clinic_id() and private.current_staff_role() = ''owner'')',
            table_name || '_owner_all', table_name
        );
    end loop;
end
$$;

-- Remove only policy names observed in the live catalog or defined by the
-- canonical chain for the reviewed problem areas.
do $$
declare
    policy_spec text[];
begin
    foreach policy_spec slice 1 in array array[
        array['dentist_schedules', 'dentist_schedules_read'],
        array['dentist_schedules', 'dentist_schedule_manage'],
        array['dentist_schedules', 'schedules_role_select'],
        array['dentist_schedules', 'schedules_manage_any'],
        array['dentist_schedules', 'schedules_manage_own'],
        array['schedule_overrides', 'schedule_overrides_read'],
        array['schedule_overrides', 'schedule_overrides_manage'],
        array['schedule_overrides', 'schedule_overrides_role_select'],
        array['schedule_overrides', 'schedule_overrides_manage_any'],
        array['schedule_overrides', 'schedule_overrides_manage_own'],
        array['contacts', 'contacts_staff_read'],
        array['contacts', 'contacts_operational_manage'],
        array['contacts', 'contacts_authorized_select'],
        array['contacts', 'contacts_receptionist_manage'],
        array['patients', 'patients_staff_read'],
        array['patients', 'patients_operational_manage'],
        array['patients', 'patients_authorized_select'],
        array['patients', 'patients_receptionist_manage'],
        array['conversations', 'conversations_staff'],
        array['conversations', 'conversations_receptionist_all'],
        array['conversations', 'conversations_authorized_select'],
        array['messages', 'messages_staff_read'],
        array['messages', 'messages_receptionist_all'],
        array['messages', 'messages_authorized_select'],
        array['booking_requests', 'booking_requests_operational'],
        array['booking_requests', 'booking_requests_read'],
        array['booking_requests', 'booking_requests_receptionist_select'],
        array['booking_requests', 'booking_requests_receptionist_insert'],
        array['booking_requests', 'booking_requests_receptionist_update'],
        array['booking_requests', 'booking_requests_payment_select'],
        array['doctor_status_change_requests', 'doctor_requests_requester_select'],
        array['doctor_status_change_requests', 'doctor_requests_doctor_insert'],
        array['appointment_change_options', 'appointment_change_options_read'],
        array['appointment_change_options', 'change_options_authorized_select'],
        array['appointment_change_options', 'change_options_receptionist_all'],
        array['handoffs', 'handoffs_staff'],
        array['handoffs', 'handoffs_receptionist_all'],
        array['reminder_consents', 'reminder_consents_staff'],
        array['reminder_consents', 'reminder_consents_receptionist_all'],
        array['knowledge_documents', 'knowledge_documents_read'],
        array['knowledge_chunks', 'knowledge_chunks_read']
    ] loop
        execute format('drop policy if exists %I on public.%I', policy_spec[2], policy_spec[1]);
    end loop;
end
$$;

create policy schedules_role_select on public.dentist_schedules
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (dentist_id = private.current_doctor_id() or private.has_permission('schedule.manage_any'))
);

create policy schedules_manage_any on public.dentist_schedules
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('schedule.manage_any'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('schedule.manage_any'));

create policy schedules_manage_own on public.dentist_schedules
for all to authenticated
using (
    clinic_id = private.current_clinic_id()
    and dentist_id = private.current_doctor_id()
    and private.has_permission('schedule.manage_own')
)
with check (
    clinic_id = private.current_clinic_id()
    and dentist_id = private.current_doctor_id()
    and private.has_permission('schedule.manage_own')
);

create policy schedule_overrides_role_select on public.schedule_overrides
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (dentist_id = private.current_doctor_id() or private.has_permission('schedule.manage_any'))
);

create policy schedule_overrides_manage_any on public.schedule_overrides
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('schedule.manage_any'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('schedule.manage_any'));

create policy schedule_overrides_manage_own on public.schedule_overrides
for all to authenticated
using (
    clinic_id = private.current_clinic_id()
    and dentist_id = private.current_doctor_id()
    and private.has_permission('schedule.manage_own')
)
with check (
    clinic_id = private.current_clinic_id()
    and dentist_id = private.current_doctor_id()
    and private.has_permission('schedule.manage_own')
);

create policy contacts_authorized_select on public.contacts
for select to authenticated
using (clinic_id = private.current_clinic_id() and private.can_view_contact(id));

create policy contacts_receptionist_manage on public.contacts
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy patients_authorized_select on public.patients
for select to authenticated
using (clinic_id = private.current_clinic_id() and private.can_view_patient(id));

create policy patients_receptionist_manage on public.patients
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy conversations_authorized_select on public.conversations
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and private.can_view_contact(contact_id)
);

create policy conversations_receptionist_all on public.conversations
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy messages_authorized_select on public.messages
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and exists (
        select 1
          from public.conversations as c
         where c.id = conversation_id
           and c.clinic_id = messages.clinic_id
           and private.can_view_contact(c.contact_id)
    )
);

create policy messages_receptionist_all on public.messages
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy booking_requests_receptionist_select on public.booking_requests
for select to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('appointment.create'));

create policy booking_requests_receptionist_insert on public.booking_requests
for insert to authenticated
with check (
    clinic_id = private.current_clinic_id()
    and private.has_permission('appointment.create')
    and status in ('awaiting_patient_info', 'awaiting_payment')
    and not payment_exempt
    and payment_exempt_reason is null
    and payment_exempted_by_staff_id is null
);

create policy booking_requests_receptionist_update on public.booking_requests
for update to authenticated
using (
    clinic_id = private.current_clinic_id()
    and private.has_permission('appointment.create')
    and not payment_exempt
)
with check (
    clinic_id = private.current_clinic_id()
    and private.has_permission('appointment.create')
    and not payment_exempt
    and payment_exempt_reason is null
    and payment_exempted_by_staff_id is null
);

create policy booking_requests_payment_select on public.booking_requests
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and private.has_permission('payment.verify')
    and not payment_exempt
    and status in ('payment_submitted', 'payment_rejected', 'payment_verified')
);

create policy doctor_requests_requester_select on public.doctor_status_change_requests
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and requested_by_staff_id = private.current_staff_id()
);

create policy doctor_requests_doctor_insert on public.doctor_status_change_requests
for insert to authenticated
with check (
    clinic_id = private.current_clinic_id()
    and requested_by_staff_id = private.current_staff_id()
    and dentist_id = private.current_doctor_id()
    and status = 'pending'
);

create policy change_options_authorized_select on public.appointment_change_options
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and exists (
        select 1
          from public.appointment_change_requests as acr
         where acr.id = change_request_id
           and acr.clinic_id = appointment_change_options.clinic_id
           and (
               private.has_permission('appointment.view_all')
               or exists (
                   select 1
                     from public.appointments as a
                    where a.id = acr.appointment_id
                      and a.clinic_id = acr.clinic_id
                      and a.dentist_id = private.current_doctor_id()
               )
           )
    )
);

create policy change_options_receptionist_all on public.appointment_change_options
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'));

create policy handoffs_receptionist_all on public.handoffs
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy reminder_consents_receptionist_all on public.reminder_consents
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

-- Knowledge content is owner-managed through the Data API. The trusted
-- backend retains service-role access for ingestion and retrieval.

-- Existing private buckets are retained. Policies are replaced only for the
-- verified bucket names and require a clinic UUID as the first path segment.
insert into storage.buckets (id, name, public, file_size_limit)
values
    ('payment-proofs', 'payment-proofs', false, 10485760),
    ('clinic-knowledge', 'clinic-knowledge', false, 52428800)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit;

drop policy if exists payment_proof_staff_read on storage.objects;
drop policy if exists payment_proof_staff_insert on storage.objects;
drop policy if exists payment_proofs_authorized_read on storage.objects;
drop policy if exists payment_proofs_authorized_insert on storage.objects;
drop policy if exists payment_proofs_authorized_update on storage.objects;
drop policy if exists clinic_knowledge_owner_read on storage.objects;
drop policy if exists clinic_knowledge_owner_write on storage.objects;
drop policy if exists clinic_knowledge_owner_insert on storage.objects;
drop policy if exists clinic_knowledge_owner_update on storage.objects;
drop policy if exists clinic_knowledge_owner_delete on storage.objects;

create policy payment_proofs_authorized_read on storage.objects
for select to authenticated
using (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.view_proof')
        or private.has_permission('payment.verify')
    )
);

create policy payment_proofs_authorized_insert on storage.objects
for insert to authenticated
with check (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
);

create policy payment_proofs_authorized_update on storage.objects
for update to authenticated
using (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
)
with check (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
);

create policy clinic_knowledge_owner_read on storage.objects
for select to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_insert on storage.objects
for insert to authenticated
with check (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_update on storage.objects
for update to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
)
with check (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_delete on storage.objects
for delete to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

alter table public.appointments replica identity full;
alter table public.appointment_visits replica identity full;
alter table public.booking_requests replica identity full;
alter table public.payments replica identity full;
alter table public.schedule_overrides replica identity full;
alter table public.appointment_change_requests replica identity full;
alter table public.handoffs replica identity full;

-- Keep the verified publication set unchanged. No sensitive table is added.

-- Reviewed indexes map to repository queries, joins, tenant/status filters,
-- or foreign keys used by the operational API. Existing GiST/HNSW indexes are
-- intentionally untouched.
drop index if exists public.patients_clinic_name_idx;
create index patients_clinic_name_idx
    on public.patients(clinic_id, lower(full_name));
create index if not exists booking_requests_contact_idx
    on public.booking_requests(contact_id);
drop index if exists public.payments_status_idx;
create index payments_status_idx
    on public.payments(clinic_id, status);
drop index if exists public.appointments_status_idx;
create index appointments_status_idx
    on public.appointments(clinic_id, status);
create index if not exists appointments_future_dentist_idx
    on public.appointments(dentist_id, start_at, status)
    where status in ('pending', 'confirmed');
create index if not exists outbound_notifications_booking_idx
    on public.outbound_notifications(booking_request_id);
create unique index if not exists doctor_status_requests_one_open_idx
    on public.doctor_status_change_requests(dentist_id)
    where status in ('pending', 'approved');
create index if not exists doctor_status_requests_requester_idx
    on public.doctor_status_change_requests(requested_by_staff_id);
create index if not exists doctor_status_requests_reviewer_idx
    on public.doctor_status_change_requests(reviewed_by_staff_id)
    where reviewed_by_staff_id is not null;
create index if not exists dentist_services_service_idx
    on public.dentist_services(service_id);
create index if not exists booking_requests_preferred_dentist_idx
    on public.booking_requests(preferred_dentist_id)
    where preferred_dentist_id is not null;
create index if not exists appointment_audit_appointment_created_idx
    on public.appointment_audit_log(appointment_id, created_at);
create index if not exists appointment_audit_patient_created_idx
    on public.appointment_audit_log(patient_id, created_at);
create index if not exists schedule_overrides_creator_idx
    on public.schedule_overrides(created_by_staff_id);
create index if not exists appointment_visits_staff_idx
    on public.appointment_visits(updated_by_staff_id)
    where updated_by_staff_id is not null;
create index if not exists appointment_change_requests_staff_idx
    on public.appointment_change_requests(requested_by_staff_id)
    where requested_by_staff_id is not null;
create index if not exists appointment_change_requests_old_dentist_idx
    on public.appointment_change_requests(old_dentist_id)
    where old_dentist_id is not null;
create index if not exists appointment_change_options_dentist_idx
    on public.appointment_change_options(dentist_id);
create index if not exists outbound_notifications_contact_idx
    on public.outbound_notifications(contact_id);
create index if not exists outbound_notifications_appointment_idx
    on public.outbound_notifications(appointment_id)
    where appointment_id is not null;
create index if not exists handoffs_contact_idx
    on public.handoffs(contact_id) where contact_id is not null;
create index if not exists handoffs_conversation_idx
    on public.handoffs(conversation_id) where conversation_id is not null;
create index if not exists handoffs_assignee_idx
    on public.handoffs(assigned_to_staff_id) where assigned_to_staff_id is not null;
create index if not exists tool_executions_conversation_idx
    on public.tool_executions(conversation_id) where conversation_id is not null;
create index if not exists knowledge_documents_clinic_status_idx
    on public.knowledge_documents(clinic_id, status);
create index if not exists knowledge_chunks_clinic_document_idx
    on public.knowledge_chunks(clinic_id, document_id);

-- Canonical operational RPCs replace the insecure live overloads. Staff and
-- clinic identity are derived from Auth; caller-supplied staff IDs are only
-- accepted when they exactly match the active authenticated staff record.
create or replace function public.verify_payment(
    p_payment_id uuid,
    p_amount_verified numeric,
    p_staff_user_id uuid
)
returns table (
    payment_id uuid,
    booking_request_id uuid,
    payment_status text,
    transition_applied boolean
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    payment_row public.payments%rowtype;
    actor_id uuid := private.current_staff_id();
begin
    if actor_id is null
       or actor_id <> p_staff_user_id
       or not private.has_permission('payment.verify') then
        raise exception using message = 'Payment verification is not authorized', errcode = '42501';
    end if;
    if p_amount_verified is null or p_amount_verified < 0 then
        raise exception using message = 'Verified amount must be non-negative', errcode = '22023';
    end if;

    select * into payment_row
      from public.payments
     where id = p_payment_id
       and clinic_id = private.current_clinic_id()
     for update;

    if not found then
        raise exception using message = 'Payment not found', errcode = 'P0002';
    end if;
    if payment_row.status = 'verified' then
        return query select payment_row.id, payment_row.booking_request_id, payment_row.status, false;
        return;
    end if;
    if payment_row.status <> 'submitted' then
        raise exception using message = 'Only submitted payments can be verified', errcode = 'check_violation';
    end if;

    update public.payments
       set status = 'verified',
           amount_verified = p_amount_verified,
           verified_by_staff_id = actor_id,
           verified_at = pg_catalog.now()
     where id = payment_row.id;

    update public.booking_requests
       set status = 'payment_verified'
     where id = payment_row.booking_request_id
       and status = 'payment_submitted';
    if not found then
        raise exception using
            message = 'Booking request changed concurrently or is not awaiting verification',
            errcode = '40001';
    end if;

    insert into public.appointment_audit_log (
        clinic_id, patient_id, actor_type, actor_id, action, new_values
    ) values (
        payment_row.clinic_id, payment_row.patient_id,
        private.current_staff_role(), actor_id, 'payment_verified',
        pg_catalog.jsonb_build_object('payment_id', payment_row.id, 'amount_verified', p_amount_verified)
    );

    return query select payment_row.id, payment_row.booking_request_id, 'verified'::text, true;
end;
$$;

create or replace function public.authorize_payment_exemption(
    p_booking_request_id uuid,
    p_reason text,
    p_owner_staff_id uuid
)
returns public.booking_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row public.booking_requests%rowtype;
begin
    if private.current_staff_id() is distinct from p_owner_staff_id
       or private.current_staff_role() <> 'owner' then
        raise exception using message = 'Payment exemption requires the clinic owner', errcode = '42501';
    end if;
    if nullif(pg_catalog.btrim(p_reason), '') is null then
        raise exception using message = 'Payment exemption reason is required', errcode = '22023';
    end if;

    select br.* into request_row
      from public.booking_requests as br
      join public.services as s
        on s.clinic_id = br.clinic_id
       and s.id = br.service_id
     where br.id = p_booking_request_id
       and br.clinic_id = private.current_clinic_id()
       and s.payment_required
     for update of br;

    if not found then
        raise exception using message = 'Payment-required booking request not found in clinic', errcode = 'P0002';
    end if;
    if request_row.status = 'payment_verified' and request_row.payment_exempt then
        return request_row;
    end if;
    if request_row.status <> 'awaiting_payment' then
        raise exception using message = 'Only an awaiting-payment request can be exempted', errcode = 'check_violation';
    end if;

    update public.booking_requests
       set status = 'payment_verified',
           payment_exempt = true,
           payment_exempt_reason = pg_catalog.btrim(p_reason),
           payment_exempted_by_staff_id = p_owner_staff_id
     where id = request_row.id
       and clinic_id = request_row.clinic_id
       and status = 'awaiting_payment'
    returning * into request_row;

    if not found then
        raise exception using message = 'Booking request changed concurrently', errcode = '40001';
    end if;

    insert into public.appointment_audit_log (
        clinic_id, patient_id, actor_type, actor_id, action, new_values, reason
    ) values (
        request_row.clinic_id, request_row.patient_id, 'owner', p_owner_staff_id,
        'payment_exempted',
        pg_catalog.jsonb_build_object('booking_request_id', request_row.id),
        pg_catalog.btrim(p_reason)
    );
    return request_row;
end;
$$;

create or replace function public.reject_payment(
    p_payment_id uuid,
    p_reason text,
    p_staff_user_id uuid
)
returns public.payments
language plpgsql
security definer
set search_path = ''
as $$
declare
    payment_row public.payments%rowtype;
    actor_id uuid := private.current_staff_id();
begin
    if actor_id is null
       or actor_id <> p_staff_user_id
       or not private.has_permission('payment.verify') then
        raise exception using message = 'Payment rejection is not authorized', errcode = '42501';
    end if;
    if nullif(pg_catalog.btrim(p_reason), '') is null then
        raise exception using message = 'Payment rejection reason is required', errcode = '22023';
    end if;

    select * into payment_row
      from public.payments
     where id = p_payment_id
       and clinic_id = private.current_clinic_id()
     for update;

    if not found then
        raise exception using message = 'Payment not found', errcode = 'P0002';
    end if;
    if payment_row.status = 'rejected' and payment_row.rejection_reason = pg_catalog.btrim(p_reason) then
        return payment_row;
    end if;
    if payment_row.status <> 'submitted' then
        raise exception using message = 'Only submitted payments can be rejected', errcode = 'check_violation';
    end if;

    update public.payments
       set status = 'rejected',
           rejection_reason = pg_catalog.btrim(p_reason),
           verified_by_staff_id = actor_id,
           verified_at = pg_catalog.now()
     where id = payment_row.id
    returning * into payment_row;

    update public.booking_requests
       set status = 'payment_rejected'
     where id = payment_row.booking_request_id
       and clinic_id = payment_row.clinic_id
       and status = 'payment_submitted';
    if not found then
        raise exception using
            message = 'Booking request changed concurrently or is not awaiting verification',
            errcode = '40001';
    end if;

    insert into public.appointment_audit_log (
        clinic_id, patient_id, actor_type, actor_id, action, new_values, reason
    ) values (
        payment_row.clinic_id, payment_row.patient_id,
        private.current_staff_role(), actor_id, 'payment_rejected',
        pg_catalog.jsonb_build_object('payment_id', payment_row.id),
        pg_catalog.btrim(p_reason)
    );
    return payment_row;
end;
$$;

create or replace function public.transition_booking_request(
    p_booking_request_id uuid,
    p_expected_status text,
    p_target_status text
)
returns public.booking_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    result public.booking_requests%rowtype;
begin
    if not private.is_service_role()
       and not private.has_permission('appointment.create') then
        raise exception using message = 'Booking transition is not authorized', errcode = '42501';
    end if;

    if not (
        (p_expected_status = 'awaiting_patient_info' and p_target_status in ('awaiting_payment', 'cancelled'))
        or (p_expected_status = 'awaiting_payment' and p_target_status in ('payment_submitted', 'expired', 'cancelled'))
        or (p_expected_status = 'payment_rejected' and p_target_status in ('payment_submitted', 'expired', 'cancelled'))
        or (p_expected_status = 'payment_verified' and p_target_status in ('awaiting_slot_selection', 'cancelled'))
        or (p_expected_status = 'awaiting_slot_selection' and p_target_status = 'cancelled')
    ) then
        raise exception using message = 'Invalid booking request transition', errcode = 'check_violation';
    end if;

    update public.booking_requests
       set status = p_target_status
     where id = p_booking_request_id
       and (private.is_service_role() or clinic_id = private.current_clinic_id())
       and status = p_expected_status
    returning * into result;

    if not found then
        raise exception using message = 'Booking request changed concurrently or was not found', errcode = '40001';
    end if;
    return result;
end;
$$;

create or replace function public.reserve_appointment(
    p_clinic_id uuid,
    p_booking_request_id uuid,
    p_payment_id uuid,
    p_dentist_id uuid,
    p_start_at timestamptz,
    p_end_at timestamptz,
    p_idempotency_key uuid
)
returns public.appointments
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row public.booking_requests%rowtype;
    result public.appointments%rowtype;
begin
    if not private.is_service_role() then
        raise exception using message = 'Appointment reservation requires the trusted server role', errcode = '42501';
    end if;
    if p_clinic_id is null or p_booking_request_id is null or p_dentist_id is null
       or p_start_at is null or p_end_at is null or p_idempotency_key is null then
        raise exception using message = 'Reservation arguments must not be null', errcode = '22023';
    end if;
    if p_end_at <= p_start_at then
        raise exception using message = 'Appointment end must be later than start', errcode = '22023';
    end if;

    select * into result
      from public.appointments
     where idempotency_key = p_idempotency_key;
    if found then
        if result.clinic_id <> p_clinic_id
           or result.booking_request_id <> p_booking_request_id
           or result.payment_id is distinct from p_payment_id
           or result.dentist_id <> p_dentist_id
           or result.start_at <> p_start_at
           or result.end_at <> p_end_at then
            raise exception using message = 'Idempotency key belongs to another reservation', errcode = '23505';
        end if;
        return result;
    end if;

    select * into request_row
      from public.booking_requests
     where id = p_booking_request_id
       and clinic_id = p_clinic_id
     for update;
    if not found then
        raise exception using message = 'Booking request not found', errcode = 'P0002';
    end if;
    if request_row.status not in ('payment_verified', 'awaiting_slot_selection')
       and not request_row.payment_exempt then
        raise exception using message = 'Booking request is not ready for reservation', errcode = 'check_violation';
    end if;

    if not exists (
        select 1
          from public.dentists as d
          join public.dentist_services as ds
            on ds.clinic_id = d.clinic_id
           and ds.dentist_id = d.id
           and ds.service_id = request_row.service_id
         where d.id = p_dentist_id
           and d.clinic_id = p_clinic_id
           and d.active
    ) then
        raise exception using message = 'Dentist is inactive, outside the clinic, or unqualified', errcode = 'check_violation';
    end if;

    insert into public.appointments (
        clinic_id, patient_id, contact_id, booking_request_id, payment_id,
        service_id, dentist_id, start_at, end_at, status, idempotency_key
    ) values (
        request_row.clinic_id, request_row.patient_id, request_row.contact_id,
        request_row.id, p_payment_id, request_row.service_id, p_dentist_id,
        p_start_at, p_end_at, 'pending', p_idempotency_key
    ) returning * into result;

    return result;
end;
$$;

create or replace function public.confirm_appointment_reservation(
    p_clinic_id uuid,
    p_appointment_id uuid,
    p_google_calendar_event_id text
)
returns public.appointments
language plpgsql
security definer
set search_path = ''
as $$
declare
    result public.appointments%rowtype;
begin
    if not private.is_service_role() then
        raise exception using message = 'Appointment confirmation requires the trusted server role', errcode = '42501';
    end if;
    if nullif(pg_catalog.btrim(p_google_calendar_event_id), '') is null then
        raise exception using message = 'Google Calendar event ID is required', errcode = '22023';
    end if;

    select * into result
      from public.appointments
     where id = p_appointment_id
       and clinic_id = p_clinic_id
     for update;
    if not found then
        raise exception using message = 'Appointment was not found in the clinic', errcode = 'P0002';
    end if;
    if result.status = 'confirmed'
       and result.google_calendar_event_id = p_google_calendar_event_id then
        return result;
    end if;
    if result.status <> 'pending' then
        raise exception using message = 'Appointment is not pending confirmation', errcode = '40001';
    end if;

    update public.appointments
       set status = 'confirmed', google_calendar_event_id = p_google_calendar_event_id
     where id = p_appointment_id
       and clinic_id = p_clinic_id
       and status = 'pending'
    returning * into result;
    if not found then
        raise exception using message = 'Pending appointment changed concurrently or was not found', errcode = '40001';
    end if;

    if result.booking_request_id is not null then
        update public.booking_requests
           set status = 'converted_to_appointment'
         where id = result.booking_request_id
           and clinic_id = result.clinic_id
           and status in ('payment_verified', 'awaiting_slot_selection');
        if not found then
            raise exception using message = 'Booking request changed concurrently', errcode = '40001';
        end if;
    end if;
    return result;
end;
$$;

create or replace function public.transition_queue_state(
    p_visit_id uuid,
    p_expected_status text,
    p_target_status text,
    p_staff_user_id uuid
)
returns public.appointment_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
    visit_row public.appointment_visits%rowtype;
    appointment_row public.appointments%rowtype;
begin
    if p_staff_user_id is null
       or private.current_staff_id() is distinct from p_staff_user_id then
        raise exception using message = 'Queue actor is invalid', errcode = '42501';
    end if;

    select * into visit_row
      from public.appointment_visits
     where id = p_visit_id
       and clinic_id = private.current_clinic_id()
     for update;
    if not found then
        raise exception using message = 'Visit not found', errcode = 'P0002';
    end if;

    select * into appointment_row
      from public.appointments
     where id = visit_row.appointment_id
       and clinic_id = visit_row.clinic_id;
    if not found then
        raise exception using message = 'Visit appointment not found', errcode = 'P0002';
    end if;

    if not (
        private.has_permission('queue.update_all')
        or (
            private.has_permission('queue.update_own')
            and appointment_row.dentist_id = private.current_doctor_id()
        )
    ) then
        raise exception using message = 'Queue update is not authorized', errcode = '42501';
    end if;

    update public.appointment_visits
       set queue_status = p_target_status,
           updated_by_staff_id = p_staff_user_id
     where id = p_visit_id
       and queue_status = p_expected_status
    returning * into visit_row;
    if not found then
        raise exception using message = 'Visit changed concurrently', errcode = '40001';
    end if;

    if p_target_status = 'completed' then
        update public.appointments
           set status = 'completed', completed_by_staff_id = p_staff_user_id
         where id = appointment_row.id
           and clinic_id = visit_row.clinic_id
           and status = 'confirmed';
        if not found then
            raise exception using message = 'Confirmed appointment changed concurrently', errcode = '40001';
        end if;
    elsif p_target_status = 'no_show' then
        update public.appointments
           set status = 'no_show'
         where id = appointment_row.id
           and clinic_id = visit_row.clinic_id
           and status = 'confirmed';
        if not found then
            raise exception using message = 'Confirmed appointment changed concurrently', errcode = '40001';
        end if;
    end if;

    insert into public.appointment_audit_log (
        clinic_id, appointment_id, patient_id, actor_type, actor_id,
        action, old_values, new_values
    ) values (
        appointment_row.clinic_id, appointment_row.id, appointment_row.patient_id,
        private.current_staff_role(), p_staff_user_id,
        case p_target_status when 'in_progress' then 'queue_started' else 'queue_' || p_target_status end,
        pg_catalog.jsonb_build_object('queue_status', p_expected_status),
        pg_catalog.jsonb_build_object('queue_status', p_target_status)
    );

    return visit_row;
end;
$$;

create or replace function public.claim_outbound_notification()
returns public.outbound_notifications
language plpgsql
security definer
set search_path = ''
as $$
declare
    result public.outbound_notifications%rowtype;
begin
    if not private.is_service_role() then
        raise exception using message = 'Notification claiming requires the trusted server role', errcode = '42501';
    end if;

    select * into result
      from public.outbound_notifications
     where status = 'pending'
       and scheduled_at <= pg_catalog.now()
     order by scheduled_at, created_at
     for update skip locked
     limit 1;

    if not found then
        return null;
    end if;

    update public.outbound_notifications
       set status = 'processing', attempt_count = attempt_count + 1
     where id = result.id
    returning * into result;
    return result;
end;
$$;

create or replace function public.deactivate_doctor(
    p_request_id uuid,
    p_owner_staff_id uuid
)
returns public.dentists
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row public.doctor_status_change_requests%rowtype;
    result public.dentists%rowtype;
begin
    if private.current_staff_id() is distinct from p_owner_staff_id
       or private.current_staff_role() <> 'owner' then
        raise exception using message = 'Doctor deactivation requires the clinic owner', errcode = '42501';
    end if;

    select * into request_row
      from public.doctor_status_change_requests
     where id = p_request_id
       and clinic_id = private.current_clinic_id()
       and status = 'approved'
     for update;
    if not found then
        raise exception using message = 'Approved deactivation request not found', errcode = 'P0002';
    end if;

    update public.dentists
       set active = false,
           deactivated_at = pg_catalog.now(),
           deactivated_by_staff_id = p_owner_staff_id,
           deactivation_reason = request_row.reason
     where id = request_row.dentist_id
       and clinic_id = request_row.clinic_id
       and active
    returning * into result;
    if not found then
        raise exception using message = 'Dentist is already inactive or missing', errcode = 'check_violation';
    end if;

    update public.doctor_status_change_requests
       set status = 'applied',
           reviewed_by_staff_id = p_owner_staff_id,
           reviewed_at = coalesce(reviewed_at, pg_catalog.now())
     where id = request_row.id;
    return result;
end;
$$;

revoke all on function public.verify_payment(uuid, numeric, uuid) from public, anon, authenticated, service_role;
revoke all on function public.authorize_payment_exemption(uuid, text, uuid) from public, anon, authenticated, service_role;
revoke all on function public.reject_payment(uuid, text, uuid) from public, anon, authenticated, service_role;
revoke all on function public.transition_booking_request(uuid, text, text) from public, anon, authenticated, service_role;
revoke all on function public.reserve_appointment(uuid, uuid, uuid, uuid, timestamptz, timestamptz, uuid) from public, anon, authenticated, service_role;
revoke all on function public.confirm_appointment_reservation(uuid, uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.transition_queue_state(uuid, text, text, uuid) from public, anon, authenticated, service_role;
revoke all on function public.claim_outbound_notification() from public, anon, authenticated, service_role;
revoke all on function public.deactivate_doctor(uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.match_knowledge_chunks(uuid, extensions.vector, double precision, integer) from public, anon, authenticated, service_role;

grant execute on function public.verify_payment(uuid, numeric, uuid) to authenticated;
grant execute on function public.authorize_payment_exemption(uuid, text, uuid) to authenticated;
grant execute on function public.reject_payment(uuid, text, uuid) to authenticated;
grant execute on function public.transition_booking_request(uuid, text, text) to authenticated, service_role;
grant execute on function public.reserve_appointment(uuid, uuid, uuid, uuid, timestamptz, timestamptz, uuid) to service_role;
grant execute on function public.confirm_appointment_reservation(uuid, uuid, text) to service_role;
grant execute on function public.transition_queue_state(uuid, text, text, uuid) to authenticated;
grant execute on function public.claim_outbound_notification() to service_role;
grant execute on function public.deactivate_doctor(uuid, uuid) to authenticated;
grant execute on function public.match_knowledge_chunks(uuid, extensions.vector, double precision, integer) to service_role;

commit;
