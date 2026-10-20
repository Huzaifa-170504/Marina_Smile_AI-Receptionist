begin;

create index dentists_clinic_active_idx on public.dentists(clinic_id, active);
create index dentist_schedules_lookup_idx on public.dentist_schedules(dentist_id, weekday, active);
create index services_clinic_active_idx on public.services(clinic_id, active);
create index patients_clinic_name_idx on public.patients(clinic_id, lower(full_name));
create index patients_primary_contact_idx on public.patients(primary_contact_id);
create index conversations_contact_status_idx on public.conversations(clinic_id, contact_id, status);
create index messages_conversation_created_idx on public.messages(conversation_id, created_at);
create index booking_requests_clinic_status_idx on public.booking_requests(clinic_id, status);
create index booking_requests_patient_idx on public.booking_requests(patient_id);
create index booking_requests_contact_idx on public.booking_requests(contact_id);
create index booking_requests_expiry_idx on public.booking_requests(expires_at)
    where status not in ('converted_to_appointment', 'expired', 'cancelled');
create index payments_booking_request_idx on public.payments(booking_request_id);
create index payments_patient_idx on public.payments(patient_id);
create index payments_status_idx on public.payments(clinic_id, status);
create index appointments_clinic_start_idx on public.appointments(clinic_id, start_at);
create index appointments_dentist_start_idx on public.appointments(dentist_id, start_at);
create index appointments_contact_start_idx on public.appointments(contact_id, start_at);
create index appointments_patient_start_idx on public.appointments(patient_id, start_at);
create index appointments_status_idx on public.appointments(clinic_id, status);
create index appointments_future_dentist_idx on public.appointments(dentist_id, start_at, status)
    where status in ('pending', 'confirmed');
create index appointment_visits_queue_idx on public.appointment_visits(clinic_id, queue_status);
create index schedule_overrides_window_idx on public.schedule_overrides(dentist_id, start_at, end_at);
create index appointment_change_requests_status_idx on public.appointment_change_requests(appointment_id, status);
create index outbound_notifications_claim_idx on public.outbound_notifications(status, scheduled_at);
create index outbound_notifications_booking_idx on public.outbound_notifications(booking_request_id);
create index handoffs_clinic_status_idx on public.handoffs(clinic_id, status);
create index staff_users_clinic_role_idx on public.staff_users(clinic_id, role);
create index staff_users_doctor_idx on public.staff_users(doctor_id) where doctor_id is not null;
create index staff_permission_overrides_staff_idx on public.staff_permission_overrides(staff_user_id);
create unique index doctor_status_requests_one_open_idx
    on public.doctor_status_change_requests(dentist_id)
    where status in ('pending', 'approved');
create index doctor_status_requests_requester_idx on public.doctor_status_change_requests(requested_by_staff_id);
create index doctor_status_requests_reviewer_idx on public.doctor_status_change_requests(reviewed_by_staff_id)
    where reviewed_by_staff_id is not null;
create index dentist_services_service_idx on public.dentist_services(service_id);
create index booking_requests_preferred_dentist_idx on public.booking_requests(preferred_dentist_id)
    where preferred_dentist_id is not null;
create index appointment_audit_appointment_created_idx on public.appointment_audit_log(appointment_id, created_at);
create index appointment_audit_patient_created_idx on public.appointment_audit_log(patient_id, created_at);
create index schedule_overrides_creator_idx on public.schedule_overrides(created_by_staff_id);
create index appointment_visits_staff_idx on public.appointment_visits(updated_by_staff_id)
    where updated_by_staff_id is not null;
create index appointment_change_requests_staff_idx on public.appointment_change_requests(requested_by_staff_id)
    where requested_by_staff_id is not null;
create index appointment_change_requests_old_dentist_idx on public.appointment_change_requests(old_dentist_id)
    where old_dentist_id is not null;
create index appointment_change_requests_old_service_idx on public.appointment_change_requests(old_service_id)
    where old_service_id is not null;
create index appointment_change_options_dentist_idx on public.appointment_change_options(dentist_id);
create index outbound_notifications_contact_idx on public.outbound_notifications(contact_id);
create index outbound_notifications_appointment_idx on public.outbound_notifications(appointment_id)
    where appointment_id is not null;
create index handoffs_contact_idx on public.handoffs(contact_id) where contact_id is not null;
create index handoffs_conversation_idx on public.handoffs(conversation_id) where conversation_id is not null;
create index handoffs_assignee_idx on public.handoffs(assigned_to_staff_id) where assigned_to_staff_id is not null;
create index tool_executions_conversation_idx on public.tool_executions(conversation_id) where conversation_id is not null;
create index knowledge_documents_clinic_status_idx on public.knowledge_documents(clinic_id, status);
create index knowledge_chunks_clinic_document_idx on public.knowledge_chunks(clinic_id, document_id);

create or replace function private.validate_booking_request_transition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.status is not distinct from old.status then
        return new;
    end if;
    if not (
        (old.status = 'awaiting_patient_info' and new.status in ('awaiting_payment', 'cancelled'))
        or (old.status = 'awaiting_payment' and new.status in ('payment_submitted', 'expired', 'cancelled'))
        or (old.status = 'awaiting_payment' and new.status = 'payment_verified' and new.payment_exempt)
        or (old.status = 'payment_submitted' and new.status in ('payment_verified', 'payment_rejected', 'cancelled'))
        or (old.status = 'payment_rejected' and new.status in ('payment_submitted', 'expired', 'cancelled'))
        or (old.status = 'payment_verified' and new.status in ('awaiting_slot_selection', 'converted_to_appointment', 'cancelled'))
        or (old.status = 'awaiting_slot_selection' and new.status in ('converted_to_appointment', 'cancelled'))
    ) then
        raise exception using
            message = format('Invalid booking request transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create or replace function private.validate_payment_transition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.status is not distinct from old.status then
        return new;
    end if;
    if not (
        (old.status = 'awaiting' and new.status = 'submitted')
        or (old.status = 'submitted' and new.status in ('verified', 'rejected'))
        or (old.status = 'verified' and new.status = 'refund_required')
        or (old.status = 'refund_required' and new.status = 'refund_processing')
        or (old.status = 'refund_processing' and new.status = 'refunded')
    ) then
        raise exception using
            message = format('Invalid payment transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create or replace function private.validate_notification_transition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.status is not distinct from old.status then
        return new;
    end if;
    if not (
        (old.status = 'pending' and new.status in ('processing', 'cancelled'))
        or (old.status = 'processing' and new.status in ('sent', 'failed', 'pending', 'cancelled'))
        or (old.status = 'failed' and new.status in ('pending', 'cancelled'))
    ) then
        raise exception using
            message = format('Invalid notification transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create or replace function private.validate_doctor_status_request()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.status <> 'pending' or new.reviewed_by_staff_id is not null or new.reviewed_at is not null then
            raise exception using message = 'A doctor status request must start pending and unreviewed', errcode = 'check_violation';
        end if;
        return new;
    end if;

    if new.clinic_id is distinct from old.clinic_id
       or new.dentist_id is distinct from old.dentist_id
       or new.requested_by_staff_id is distinct from old.requested_by_staff_id
       or new.requested_status is distinct from old.requested_status
       or new.reason is distinct from old.reason then
        raise exception using message = 'Doctor status request details are immutable', errcode = 'check_violation';
    end if;

    if new.status is distinct from old.status and not (
        (old.status = 'pending' and new.status in ('approved', 'rejected', 'cancelled'))
        or (old.status = 'approved' and new.status = 'applied')
    ) then
        raise exception using
            message = pg_catalog.format('Invalid doctor status request transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;

    if old.reviewed_by_staff_id is not null and (
        new.reviewed_by_staff_id is distinct from old.reviewed_by_staff_id
        or new.reviewed_at is distinct from old.reviewed_at
    ) then
        raise exception using message = 'Doctor status review attribution is immutable', errcode = 'check_violation';
    end if;
    if new.status in ('approved', 'rejected', 'applied')
       and (new.reviewed_by_staff_id is null or new.reviewed_at is null) then
        raise exception using message = 'Reviewed doctor status requests require reviewer attribution', errcode = 'check_violation';
    end if;
    if new.status in ('approved', 'rejected', 'applied') and not exists (
        select 1
          from public.staff_users as reviewer
         where reviewer.id = new.reviewed_by_staff_id
           and reviewer.clinic_id = new.clinic_id
           and reviewer.role = 'owner'
    ) then
        raise exception using message = 'Doctor status requests must be reviewed by a clinic owner', errcode = 'check_violation';
    end if;
    if new.status in ('pending', 'cancelled')
       and (new.reviewed_by_staff_id is not null or new.reviewed_at is not null) then
        raise exception using message = 'Unreviewed doctor status requests cannot have reviewer attribution', errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create or replace function private.validate_appointment_change_request()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.status <> 'pending' or new.resolved_at is not null then
            raise exception using message = 'An appointment change request must start pending and unresolved', errcode = 'check_violation';
        end if;
        return new;
    end if;

    if new.clinic_id is distinct from old.clinic_id
       or new.appointment_id is distinct from old.appointment_id
       or new.requested_by_staff_id is distinct from old.requested_by_staff_id
       or new.requested_by_actor_type is distinct from old.requested_by_actor_type
       or new.change_type is distinct from old.change_type
       or new.old_start_at is distinct from old.old_start_at
       or new.old_end_at is distinct from old.old_end_at
       or new.old_dentist_id is distinct from old.old_dentist_id
       or new.old_service_id is distinct from old.old_service_id
       or new.old_google_calendar_event_id is distinct from old.old_google_calendar_event_id then
        raise exception using message = 'Appointment change request identity and snapshot are immutable', errcode = 'check_violation';
    end if;

    if new.status is distinct from old.status and not (
        (old.status = 'pending' and new.status in ('accepted', 'rejected', 'expired', 'cancelled'))
        or (old.status = 'accepted' and new.status = 'applied')
    ) then
        raise exception using
            message = pg_catalog.format('Invalid appointment change transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;

    if new.status in ('rejected', 'expired', 'applied', 'cancelled') and new.resolved_at is null then
        new.resolved_at := pg_catalog.now();
    elsif new.status in ('pending', 'accepted') and new.resolved_at is not null then
        raise exception using message = 'Unresolved appointment change states cannot have resolved_at', errcode = 'check_violation';
    end if;
    if old.resolved_at is not null and new.resolved_at is distinct from old.resolved_at then
        raise exception using message = 'Appointment change resolution timestamp is immutable', errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create trigger booking_requests_validate_transition
before update of status on public.booking_requests
for each row execute function private.validate_booking_request_transition();

create trigger payments_validate_transition
before update of status on public.payments
for each row execute function private.validate_payment_transition();

create trigger outbound_notifications_validate_transition
before update of status on public.outbound_notifications
for each row execute function private.validate_notification_transition();

create trigger doctor_status_requests_validate_lifecycle
before insert or update on public.doctor_status_change_requests
for each row execute function private.validate_doctor_status_request();

create trigger appointment_change_requests_validate_lifecycle
before insert or update on public.appointment_change_requests
for each row execute function private.validate_appointment_change_request();

create or replace function private.validate_dentist_deactivation()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    approved_request_id uuid;
begin
    if tg_op = 'INSERT' and not new.active then
        raise exception using message = 'A dentist must be created active', errcode = 'check_violation';
    end if;
    if tg_op = 'INSERT' then
        return new;
    end if;
    if not old.active and new.active then
        raise exception using message = 'Dentist reactivation requires a future governed workflow', errcode = 'check_violation';
    end if;
    if old.active and not new.active then
        if exists (
            select 1 from public.appointments a
             where a.dentist_id = old.id
               and a.clinic_id = old.clinic_id
               and a.status in ('pending', 'confirmed')
               and a.start_at > now()
        ) then
            raise exception using
                message = 'Dentist has future active appointments',
                errcode = 'check_violation';
        end if;

        if new.deactivated_by_staff_id is null
           or nullif(pg_catalog.btrim(new.deactivation_reason), '') is null then
            raise exception using message = 'Doctor deactivation requires owner attribution and a reason', errcode = 'check_violation';
        end if;
        if not exists (
            select 1
              from public.staff_users as owner_staff
             where owner_staff.id = new.deactivated_by_staff_id
               and owner_staff.clinic_id = old.clinic_id
               and owner_staff.role = 'owner'
               and owner_staff.active
        ) then
            raise exception using message = 'Doctor deactivation actor must be an active clinic owner', errcode = 'check_violation';
        end if;

        select r.id into approved_request_id
          from public.doctor_status_change_requests r
             where r.clinic_id = old.clinic_id
               and r.dentist_id = old.id
               and r.requested_status = 'inactive'
               and r.status = 'approved'
             order by r.reviewed_at desc
             limit 1;
        if approved_request_id is null then
            raise exception using
                message = 'Approved doctor deactivation request is required',
                errcode = 'check_violation';
        end if;

        new.deactivated_at := pg_catalog.coalesce(new.deactivated_at, pg_catalog.now());

        update public.doctor_status_change_requests
           set status = 'applied'
         where id = approved_request_id;
    end if;
    return new;
end;
$$;

create trigger dentists_validate_deactivation
before update of active on public.dentists
for each row execute function private.validate_dentist_deactivation();

create trigger dentists_validate_initial_state
before insert on public.dentists
for each row execute function private.validate_dentist_deactivation();

create or replace function private.prevent_audit_log_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception using message = 'Appointment audit rows are append-only', errcode = '42501';
end;
$$;

create or replace function private.audit_appointment_write()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    audit_action text;
    actor_role text := pg_catalog.coalesce(private.current_staff_role(), 'system');
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

create trigger appointment_audit_log_append_only
before update or delete on public.appointment_audit_log
for each row execute function private.prevent_audit_log_mutation();

create trigger appointments_audit_write
after insert or update on public.appointments
for each row execute function private.audit_appointment_write();

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
           verified_at = now()
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
        jsonb_build_object('payment_id', payment_row.id, 'amount_verified', p_amount_verified)
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
       and (
           private.is_service_role()
           or clinic_id = private.current_clinic_id()
       )
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
    if nullif(btrim(p_google_calendar_event_id), '') is null then
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
        update public.appointments set status = 'no_show'
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
     where status = 'pending' and scheduled_at <= now()
     order by scheduled_at, created_at
     for update skip locked
     limit 1;

    if not found then return null; end if;

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
           deactivated_at = now(),
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
       set status = 'applied', reviewed_by_staff_id = p_owner_staff_id,
           reviewed_at = coalesce(reviewed_at, now())
     where id = request_row.id;
    return result;
end;
$$;

revoke all on function private.validate_booking_request_transition() from public, anon, authenticated, service_role;
revoke all on function private.validate_payment_transition() from public, anon, authenticated, service_role;
revoke all on function private.validate_notification_transition() from public, anon, authenticated, service_role;
revoke all on function private.validate_doctor_status_request() from public, anon, authenticated, service_role;
revoke all on function private.validate_appointment_change_request() from public, anon, authenticated, service_role;
revoke all on function private.validate_dentist_deactivation() from public, anon, authenticated, service_role;
revoke all on function private.prevent_audit_log_mutation() from public, anon, authenticated, service_role;
revoke all on function private.audit_appointment_write() from public, anon, authenticated, service_role;

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
