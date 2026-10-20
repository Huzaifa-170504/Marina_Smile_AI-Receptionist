begin;

alter table public.clinics enable row level security;
alter table public.staff_users enable row level security;
alter table public.staff_permission_overrides enable row level security;
alter table public.dentists enable row level security;
alter table public.doctor_status_change_requests enable row level security;
alter table public.services enable row level security;
alter table public.dentist_services enable row level security;
alter table public.dentist_schedules enable row level security;
alter table public.contacts enable row level security;
alter table public.patients enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.booking_requests enable row level security;
alter table public.payments enable row level security;
alter table public.appointments enable row level security;
alter table public.schedule_overrides enable row level security;
alter table public.appointment_visits enable row level security;
alter table public.appointment_change_requests enable row level security;
alter table public.appointment_change_options enable row level security;
alter table public.appointment_audit_log enable row level security;
alter table public.reminder_consents enable row level security;
alter table public.outbound_notifications enable row level security;
alter table public.handoffs enable row level security;
alter table public.tool_executions enable row level security;
alter table public.knowledge_documents enable row level security;
alter table public.knowledge_chunks enable row level security;

revoke all on schema public from public, anon;
revoke all on all tables in schema public from public, anon, authenticated, service_role;
grant usage on schema public to authenticated, service_role;

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

create policy clinics_staff_select on public.clinics
for select to authenticated
using (id = private.current_clinic_id());

create policy clinics_owner_update on public.clinics
for update to authenticated
using (id = private.current_clinic_id() and private.current_staff_role() = 'owner')
with check (id = private.current_clinic_id() and private.current_staff_role() = 'owner');

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
        execute format(
            'create policy %I on public.%I for all to authenticated '
            || 'using (clinic_id = private.current_clinic_id() and private.current_staff_role() = ''owner'') '
            || 'with check (clinic_id = private.current_clinic_id() and private.current_staff_role() = ''owner'')',
            table_name || '_owner_all', table_name
        );
    end loop;
end
$$;

create policy staff_users_self_select on public.staff_users
for select to authenticated
using (auth_user_id = (select auth.uid()) and active);

create policy permission_overrides_self_select on public.staff_permission_overrides
for select to authenticated
using (staff_user_id = private.current_staff_id());

create policy dentists_clinic_select on public.dentists
for select to authenticated
using (clinic_id = private.current_clinic_id());

create policy doctor_requests_requester_select on public.doctor_status_change_requests
for select to authenticated
using (clinic_id = private.current_clinic_id() and requested_by_staff_id = private.current_staff_id());

create policy doctor_requests_doctor_insert on public.doctor_status_change_requests
for insert to authenticated
with check (
    clinic_id = private.current_clinic_id()
    and requested_by_staff_id = private.current_staff_id()
    and dentist_id = private.current_doctor_id()
    and status = 'pending'
);

create policy services_clinic_select on public.services
for select to authenticated
using (clinic_id = private.current_clinic_id());

create policy dentist_services_clinic_select on public.dentist_services
for select to authenticated
using (clinic_id = private.current_clinic_id());

create policy schedules_role_select on public.dentist_schedules
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        dentist_id = private.current_doctor_id()
        or private.has_permission('schedule.manage_any')
    )
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

create policy conversations_receptionist_all on public.conversations
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

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

create policy payments_verifier_select on public.payments
for select to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('payment.verify'));

create policy appointments_authorized_select on public.appointments
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        private.has_permission('appointment.view_all')
        or (
            private.has_permission('appointment.view_own')
            and dentist_id = private.current_doctor_id()
        )
    )
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

create policy visits_authorized_select on public.appointment_visits
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        private.has_permission('queue.view_all')
        or (
            private.has_permission('queue.view_own')
            and exists (
                select 1 from public.appointments a
                 where a.id = appointment_id
                   and a.dentist_id = private.current_doctor_id()
            )
        )
    )
);

create policy change_requests_authorized_select on public.appointment_change_requests
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        private.has_permission('appointment.view_all')
        or exists (
            select 1 from public.appointments a
             where a.id = appointment_id
               and a.dentist_id = private.current_doctor_id()
        )
    )
);

create policy change_requests_receptionist_all on public.appointment_change_requests
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'));

create policy change_options_authorized_select on public.appointment_change_options
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and exists (
        select 1
          from public.appointment_change_requests acr
         where acr.id = change_request_id
           and (
               private.has_permission('appointment.view_all')
               or exists (
                   select 1 from public.appointments a
                    where a.id = acr.appointment_id
                      and a.dentist_id = private.current_doctor_id()
               )
           )
    )
);

create policy change_options_receptionist_all on public.appointment_change_options
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('appointment.reschedule_any'));

create policy audit_authorized_select on public.appointment_audit_log
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        private.has_permission('appointment.view_all')
        or exists (
            select 1 from public.appointments a
             where a.id = appointment_id
               and a.dentist_id = private.current_doctor_id()
        )
    )
);

create policy reminder_consents_receptionist_all on public.reminder_consents
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

create policy notifications_receptionist_select on public.outbound_notifications
for select to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('appointment.view_all'));

create policy handoffs_receptionist_all on public.handoffs
for all to authenticated
using (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'))
with check (clinic_id = private.current_clinic_id() and private.has_permission('patient.view_all'));

revoke all on function public.match_knowledge_chunks(uuid, extensions.vector, double precision, integer) from public;
grant execute on function public.match_knowledge_chunks(uuid, extensions.vector, double precision, integer) to authenticated;

commit;
