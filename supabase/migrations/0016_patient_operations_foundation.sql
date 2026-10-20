begin;

-- Durable patient-workflow state. The JSON object is application state only;
-- credentials, tokens, proof bytes, and other secrets do not belong here.
alter table public.conversations
    add column workflow_mode text not null default 'GENERAL_QUERY',
    add column workflow_step text,
    add column workflow_state jsonb not null default '{}'::jsonb,
    add column resume_mode text,
    add column resume_step text,
    add column active_booking_request_id uuid,
    add column workflow_version bigint not null default 0,
    add constraint conversations_workflow_mode_check check (
        workflow_mode in ('GENERAL_QUERY', 'BOOKING', 'HANDOFF', 'APPOINTMENT_LOOKUP', 'REFUND')
    ),
    add constraint conversations_resume_mode_check check (
        resume_mode is null
        or resume_mode in ('GENERAL_QUERY', 'BOOKING', 'HANDOFF', 'APPOINTMENT_LOOKUP', 'REFUND')
    ),
    add constraint conversations_workflow_step_check check (
        workflow_step is null or nullif(btrim(workflow_step), '') is not null
    ),
    add constraint conversations_resume_step_check check (
        resume_step is null or nullif(btrim(resume_step), '') is not null
    ),
    add constraint conversations_workflow_state_object_check check (
        jsonb_typeof(workflow_state) = 'object'
    ),
    add constraint conversations_workflow_version_check check (workflow_version >= 0),
    add constraint conversations_active_booking_request_fkey
        foreign key (clinic_id, active_booking_request_id)
        references public.booking_requests(clinic_id, id) on delete restrict;

create index conversations_active_booking_request_idx
    on public.conversations(clinic_id, active_booking_request_id)
    where active_booking_request_id is not null;

alter table public.booking_requests
    add column idempotency_key uuid;

create unique index booking_requests_clinic_idempotency_unique
    on public.booking_requests(clinic_id, idempotency_key)
    where idempotency_key is not null;

alter table public.payments
    add column submission_idempotency_key uuid;

create unique index payments_clinic_submission_idempotency_unique
    on public.payments(clinic_id, submission_idempotency_key)
    where submission_idempotency_key is not null;

alter table public.outbound_notifications
    add column event_key text,
    add column template_data jsonb not null default '{}'::jsonb,
    add constraint outbound_notifications_event_key_check check (
        event_key is null or nullif(btrim(event_key), '') is not null
    ),
    add constraint outbound_notifications_template_data_object_check check (
        jsonb_typeof(template_data) = 'object'
    );

create unique index outbound_notifications_clinic_event_key_unique
    on public.outbound_notifications(clinic_id, event_key)
    where event_key is not null;

-- Patient-facing references use a global sequence. The date is informational;
-- uniqueness comes from the sequence and the unique index.
create sequence private.appointment_reference_seq as bigint start with 1 increment by 1;
revoke all on sequence private.appointment_reference_seq
    from public, anon, authenticated, service_role;

alter table public.appointments
    add column appointment_reference text;

-- The live legacy schema has the audit table but may not have its canonical
-- writer. Restore it explicitly before the reference backfill. On a clean
-- replay CREATE OR REPLACE preserves the existing function identity.
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

revoke all on function private.audit_appointment_write()
    from public, anon, authenticated, service_role;

-- Avoid a duplicate audit trigger if a legacy deployment retained the same
-- function under another trigger name. The stable canonical name is used only
-- when no appointment trigger already invokes this function.
do $$
begin
    if not exists (
        select 1
          from pg_catalog.pg_trigger as trigger_row
         where trigger_row.tgrelid = 'public.appointments'::pg_catalog.regclass
           and not trigger_row.tgisinternal
           and trigger_row.tgfoid = 'private.audit_appointment_write()'::pg_catalog.regprocedure
    ) then
        create trigger appointments_audit_write
        after insert or update on public.appointments
        for each row execute function private.audit_appointment_write();
    end if;
end;
$$;

-- This is a metadata backfill, not an operational appointment change. Locate
-- audit triggers by function identity rather than assuming a trigger name.
-- Updated-at and payment-gate triggers remain enabled and are preserved.
do $$
declare
    audit_trigger record;
begin
    for audit_trigger in
        select trigger_row.tgname
          from pg_catalog.pg_trigger as trigger_row
         where trigger_row.tgrelid = 'public.appointments'::pg_catalog.regclass
           and not trigger_row.tgisinternal
           and trigger_row.tgfoid = 'private.audit_appointment_write()'::pg_catalog.regprocedure
    loop
        execute pg_catalog.format(
            'alter table public.appointments disable trigger %I',
            audit_trigger.tgname
        );
    end loop;

    with generated_references as (
        select
            id,
            pg_catalog.nextval('private.appointment_reference_seq'::regclass) as reference_number
          from public.appointments
    )
    update public.appointments as appointment
       set appointment_reference = pg_catalog.format(
           'MS-APT-%s-%s',
           pg_catalog.to_char(appointment.created_at at time zone 'UTC', 'YYYYMMDD'),
           pg_catalog.lpad(
               generated.reference_number::text,
               greatest(
                   6,
                   pg_catalog.length(generated.reference_number::text)
               ),
               '0'
           )
       )
      from generated_references as generated
     where generated.id = appointment.id;

    for audit_trigger in
        select trigger_row.tgname
          from pg_catalog.pg_trigger as trigger_row
         where trigger_row.tgrelid = 'public.appointments'::pg_catalog.regclass
           and not trigger_row.tgisinternal
           and trigger_row.tgfoid = 'private.audit_appointment_write()'::pg_catalog.regprocedure
    loop
        execute pg_catalog.format(
            'alter table public.appointments enable trigger %I',
            audit_trigger.tgname
        );
    end loop;
end;
$$;

alter table public.appointments
    alter column appointment_reference set not null,
    add constraint appointments_reference_format_check check (
        appointment_reference ~ '^MS-APT-[0-9]{8}-[0-9]{6,}$'
    );

create unique index appointments_reference_unique
    on public.appointments(appointment_reference);

create or replace function private.manage_appointment_reference()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    reference_number bigint;
begin
    if tg_op = 'UPDATE' then
        if new.appointment_reference is distinct from old.appointment_reference then
            raise exception using
                message = 'Appointment reference is immutable',
                errcode = 'check_violation';
        end if;
        return new;
    end if;

    if new.appointment_reference is not null then
        raise exception using
            message = 'Appointment reference is generated by the server',
            errcode = 'check_violation';
    end if;

    reference_number := pg_catalog.nextval('private.appointment_reference_seq'::regclass);
    new.appointment_reference := pg_catalog.format(
        'MS-APT-%s-%s',
        pg_catalog.to_char(
            pg_catalog.coalesce(new.created_at, pg_catalog.now()) at time zone 'UTC',
            'YYYYMMDD'
        ),
        pg_catalog.lpad(
            reference_number::text,
            greatest(6, pg_catalog.length(reference_number::text)),
            '0'
        )
    );
    return new;
end;
$$;

revoke all on function private.manage_appointment_reference()
    from public, anon, authenticated, service_role;

create trigger appointments_manage_reference
before insert or update of appointment_reference on public.appointments
for each row execute function private.manage_appointment_reference();

-- Resolve permissions for an explicit staff row inside trusted database code.
-- User-facing authorization continues to call private.has_permission(), which
-- derives the staff row from auth.uid().
create or replace function private.staff_has_permission(
    p_staff_user_id uuid,
    p_permission text
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    staff_role text;
    override_value boolean;
begin
    select su.role
      into staff_role
      from public.staff_users as su
     where su.id = p_staff_user_id
       and su.active;

    if staff_role is null then
        return false;
    end if;

    select spo.allowed
      into override_value
      from public.staff_permission_overrides as spo
     where spo.staff_user_id = p_staff_user_id
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
            'schedule.manage_any', 'patient.view_all',
            'payment.verify', 'payment.view_proof',
            'refund.manage', 'refund.view_proof'
        )
        when 'payment_verifier' then p_permission in (
            'payment.verify', 'payment.view_proof'
        )
        else false
    end;
end;
$$;

create or replace function private.has_permission(p_permission text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select private.staff_has_permission(private.current_staff_id(), p_permission);
$$;

revoke all on function private.staff_has_permission(uuid, text)
    from public, anon, authenticated, service_role;
revoke all on function private.has_permission(text)
    from public, anon, authenticated, service_role;
grant execute on function private.has_permission(text) to authenticated;

create table public.refund_requests (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    appointment_id uuid not null,
    payment_id uuid not null,
    patient_id uuid not null,
    reason text not null check (nullif(btrim(reason), '') is not null),
    requested_amount numeric(12,2) not null check (requested_amount > 0),
    eligible_amount numeric(12,2) not null check (eligible_amount >= 0),
    approved_amount numeric(12,2) check (approved_amount is null or approved_amount >= 0),
    status text not null default 'requested' check (
        status in (
            'requested', 'under_review', 'approved_for_payout', 'rejected',
            'refund_processing', 'refunded', 'cancelled'
        )
    ),
    reviewed_by_staff_id uuid,
    reviewed_at timestamptz,
    rejection_reason text,
    payout_method text check (
        payout_method is null or payout_method in ('cash', 'in_person', 'online')
    ),
    payout_amount numeric(12,2) check (payout_amount is null or payout_amount >= 0),
    payout_reference text,
    proof_storage_path text,
    paid_by_staff_id uuid,
    paid_at timestamptz,
    internal_note text,
    request_idempotency_key uuid not null,
    payout_idempotency_key uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, appointment_id, payment_id),
    foreign key (clinic_id, appointment_id)
        references public.appointments(clinic_id, id) on delete restrict,
    foreign key (clinic_id, payment_id)
        references public.payments(clinic_id, id) on delete restrict,
    foreign key (clinic_id, patient_id)
        references public.patients(clinic_id, id) on delete restrict,
    foreign key (clinic_id, reviewed_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    foreign key (clinic_id, paid_by_staff_id)
        references public.staff_users(clinic_id, id) on delete restrict,
    check (approved_amount is null or approved_amount <= eligible_amount),
    check (
        payout_amount is null
        or (approved_amount is not null and payout_amount <= approved_amount)
    ),
    check (
        status <> 'approved_for_payout'
        or (
            reviewed_by_staff_id is not null
            and reviewed_at is not null
            and approved_amount is not null
        )
    ),
    check (
        status <> 'rejected'
        or (
            reviewed_by_staff_id is not null
            and reviewed_at is not null
            and nullif(btrim(rejection_reason), '') is not null
        )
    ),
    check (
        status <> 'refunded'
        or (
            paid_by_staff_id is not null
            and paid_at is not null
            and payout_method is not null
            and payout_amount is not null
            and payout_idempotency_key is not null
        )
    ),
    check (
        payout_method is distinct from 'online'
        or nullif(btrim(proof_storage_path), '') is not null
    ),
    check (
        proof_storage_path is null
        or split_part(proof_storage_path, '/', 1) = clinic_id::text
    )
);

create unique index refund_requests_clinic_request_idempotency_unique
    on public.refund_requests(clinic_id, request_idempotency_key);

create unique index refund_requests_clinic_payout_idempotency_unique
    on public.refund_requests(clinic_id, payout_idempotency_key)
    where payout_idempotency_key is not null;

create index refund_requests_clinic_status_created_idx
    on public.refund_requests(clinic_id, status, created_at);

create index refund_requests_patient_created_idx
    on public.refund_requests(patient_id, created_at);

create index refund_requests_payment_idx
    on public.refund_requests(payment_id);

create index refund_requests_reviewer_idx
    on public.refund_requests(reviewed_by_staff_id)
    where reviewed_by_staff_id is not null;

create index refund_requests_paid_by_idx
    on public.refund_requests(paid_by_staff_id)
    where paid_by_staff_id is not null;

create trigger refund_requests_set_updated_at
before update on public.refund_requests
for each row execute function public.set_updated_at();

create or replace function private.validate_refund_request()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    appointment_row public.appointments%rowtype;
    payment_row public.payments%rowtype;
    maximum_refund numeric(12,2);
begin
    if tg_op = 'UPDATE' and (
        new.clinic_id is distinct from old.clinic_id
        or new.appointment_id is distinct from old.appointment_id
        or new.payment_id is distinct from old.payment_id
        or new.patient_id is distinct from old.patient_id
        or new.request_idempotency_key is distinct from old.request_idempotency_key
    ) then
        raise exception using
            message = 'Refund identity links are immutable',
            errcode = 'check_violation';
    end if;

    select *
      into appointment_row
      from public.appointments
     where id = new.appointment_id
       and clinic_id = new.clinic_id;
    if not found then
        raise exception using message = 'Refund appointment is invalid for clinic', errcode = '23503';
    end if;

    select *
      into payment_row
      from public.payments
     where id = new.payment_id
       and clinic_id = new.clinic_id;
    if not found then
        raise exception using message = 'Refund payment is invalid for clinic', errcode = '23503';
    end if;

    if appointment_row.patient_id <> new.patient_id
       or payment_row.patient_id <> new.patient_id
       or appointment_row.payment_id is distinct from new.payment_id
       or appointment_row.booking_request_id is distinct from payment_row.booking_request_id then
        raise exception using
            message = 'Refund appointment, payment, and patient do not describe one booking',
            errcode = 'check_violation';
    end if;

    maximum_refund := pg_catalog.coalesce(payment_row.amount_verified, payment_row.amount_expected, 0);
    if new.eligible_amount > maximum_refund or new.requested_amount > new.eligible_amount then
        raise exception using
            message = 'Refund amount exceeds the eligible paid amount',
            errcode = 'check_violation';
    end if;

    if tg_op = 'INSERT' then
        if new.status <> 'requested' then
            raise exception using message = 'A refund must start as requested', errcode = 'check_violation';
        end if;
        if appointment_row.status <> 'cancelled' then
            raise exception using message = 'A refund requires a cancelled appointment', errcode = 'check_violation';
        end if;
        if payment_row.status <> 'verified' then
            raise exception using message = 'A refund requires a verified payment', errcode = 'check_violation';
        end if;
    elsif new.status is distinct from old.status and not (
        (old.status = 'requested' and new.status in ('under_review', 'approved_for_payout', 'rejected', 'cancelled'))
        or (old.status = 'under_review' and new.status in ('approved_for_payout', 'rejected', 'cancelled'))
        or (old.status = 'approved_for_payout' and new.status in ('refund_processing', 'cancelled'))
        or (old.status = 'refund_processing' and new.status = 'refunded')
    ) then
        raise exception using
            message = pg_catalog.format('Invalid refund transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;

    return new;
end;
$$;

revoke all on function private.validate_refund_request()
    from public, anon, authenticated, service_role;

create trigger refund_requests_validate_write
before insert or update on public.refund_requests
for each row execute function private.validate_refund_request();

alter table public.refund_requests enable row level security;

grant select on public.refund_requests to authenticated;
grant select, insert, update on public.refund_requests to service_role;

create policy refund_requests_staff_select on public.refund_requests
for select to authenticated
using (
    clinic_id = (select private.current_clinic_id())
    and (select private.has_permission('refund.manage'))
);

-- Patient requests are trusted-server only. Clinic and patient identity are
-- derived from the linked appointment/payment rather than accepted as input.
create or replace function public.request_refund(
    p_appointment_id uuid,
    p_payment_id uuid,
    p_reason text,
    p_requested_amount numeric,
    p_idempotency_key uuid
)
returns public.refund_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    appointment_row public.appointments%rowtype;
    payment_row public.payments%rowtype;
    existing_row public.refund_requests%rowtype;
    result public.refund_requests%rowtype;
    eligible numeric(12,2);
begin
    if not private.is_service_role() then
        raise exception using message = 'Refund requests require the trusted server role', errcode = '42501';
    end if;
    if p_idempotency_key is null
       or nullif(pg_catalog.btrim(p_reason), '') is null
       or p_requested_amount is null
       or p_requested_amount <= 0 then
        raise exception using message = 'Valid refund request details are required', errcode = '22023';
    end if;

    select *
      into appointment_row
      from public.appointments
     where id = p_appointment_id;
    if not found then
        raise exception using message = 'Appointment not found', errcode = 'P0002';
    end if;

    select *
      into payment_row
      from public.payments
     where id = p_payment_id
       and clinic_id = appointment_row.clinic_id
       and patient_id = appointment_row.patient_id
       and booking_request_id = appointment_row.booking_request_id;
    if not found or appointment_row.payment_id is distinct from payment_row.id then
        raise exception using message = 'Payment does not belong to the appointment', errcode = 'check_violation';
    end if;

    select *
      into existing_row
      from public.refund_requests
     where clinic_id = appointment_row.clinic_id
       and request_idempotency_key = p_idempotency_key;
    if found then
        if existing_row.appointment_id <> p_appointment_id
           or existing_row.payment_id <> p_payment_id
           or existing_row.reason <> pg_catalog.btrim(p_reason)
           or existing_row.requested_amount <> p_requested_amount then
            raise exception using message = 'Refund idempotency key belongs to another request', errcode = '23505';
        end if;
        return existing_row;
    end if;

    if appointment_row.status <> 'cancelled' then
        raise exception using message = 'Only a cancelled appointment is refundable', errcode = 'check_violation';
    end if;
    if payment_row.status <> 'verified' then
        raise exception using message = 'Only a verified payment is refundable', errcode = 'check_violation';
    end if;

    eligible := pg_catalog.coalesce(payment_row.amount_verified, payment_row.amount_expected, 0);
    if p_requested_amount > eligible then
        raise exception using message = 'Requested refund exceeds the eligible amount', errcode = 'check_violation';
    end if;

    insert into public.refund_requests (
        clinic_id, appointment_id, payment_id, patient_id, reason,
        requested_amount, eligible_amount, request_idempotency_key
    ) values (
        appointment_row.clinic_id, appointment_row.id, payment_row.id,
        appointment_row.patient_id, pg_catalog.btrim(p_reason),
        p_requested_amount, eligible, p_idempotency_key
    )
    returning * into result;

    insert into public.outbound_notifications (
        clinic_id, contact_id, booking_request_id, appointment_id,
        notification_type, event_key, template_data, scheduled_at
    ) values (
        appointment_row.clinic_id, appointment_row.contact_id,
        appointment_row.booking_request_id, appointment_row.id,
        'refund_requested', 'refund_requested:' || result.id::text,
        pg_catalog.jsonb_build_object(
            'refund_id', result.id,
            'appointment_reference', appointment_row.appointment_reference,
            'requested_amount', result.requested_amount
        ),
        pg_catalog.now()
    )
    on conflict (clinic_id, event_key) where event_key is not null do nothing;

    return result;
end;
$$;

create or replace function public.start_refund_review(p_refund_id uuid)
returns public.refund_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    result public.refund_requests%rowtype;
begin
    if private.current_staff_id() is null or not private.has_permission('refund.manage') then
        raise exception using message = 'Refund review is not authorized', errcode = '42501';
    end if;

    select * into result
      from public.refund_requests
     where id = p_refund_id
       and clinic_id = private.current_clinic_id()
     for update;
    if not found then
        raise exception using message = 'Refund request not found', errcode = 'P0002';
    end if;
    if result.status = 'under_review' then
        return result;
    end if;
    if result.status <> 'requested' then
        raise exception using message = 'Refund request cannot enter review', errcode = 'check_violation';
    end if;

    update public.refund_requests
       set status = 'under_review'
     where id = result.id
    returning * into result;
    return result;
end;
$$;

create or replace function public.approve_refund(
    p_refund_id uuid,
    p_approved_amount numeric
)
returns public.refund_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := private.current_staff_id();
    result public.refund_requests%rowtype;
    appointment_row public.appointments%rowtype;
begin
    if actor_id is null or not private.has_permission('refund.manage') then
        raise exception using message = 'Refund approval is not authorized', errcode = '42501';
    end if;
    if p_approved_amount is null or p_approved_amount < 0 then
        raise exception using message = 'Approved amount must be non-negative', errcode = '22023';
    end if;

    select * into result
      from public.refund_requests
     where id = p_refund_id
       and clinic_id = private.current_clinic_id()
     for update;
    if not found then
        raise exception using message = 'Refund request not found', errcode = 'P0002';
    end if;
    if result.status = 'approved_for_payout' and result.approved_amount = p_approved_amount then
        return result;
    end if;
    if result.status not in ('requested', 'under_review') then
        raise exception using message = 'Refund request cannot be approved', errcode = 'check_violation';
    end if;
    if p_approved_amount > result.eligible_amount then
        raise exception using message = 'Approved amount exceeds the eligible amount', errcode = 'check_violation';
    end if;

    update public.refund_requests
       set status = 'approved_for_payout',
           approved_amount = p_approved_amount,
           reviewed_by_staff_id = actor_id,
           reviewed_at = pg_catalog.now(),
           rejection_reason = null
     where id = result.id
    returning * into result;

    select * into appointment_row
      from public.appointments
     where id = result.appointment_id and clinic_id = result.clinic_id;

    insert into public.outbound_notifications (
        clinic_id, contact_id, booking_request_id, appointment_id,
        notification_type, event_key, template_data, scheduled_at
    ) values (
        result.clinic_id, appointment_row.contact_id,
        appointment_row.booking_request_id, appointment_row.id,
        'refund_approved', 'refund_approved:' || result.id::text,
        pg_catalog.jsonb_build_object(
            'refund_id', result.id,
            'appointment_reference', appointment_row.appointment_reference,
            'approved_amount', result.approved_amount
        ),
        pg_catalog.now()
    )
    on conflict (clinic_id, event_key) where event_key is not null do nothing;

    return result;
end;
$$;

create or replace function public.reject_refund(
    p_refund_id uuid,
    p_reason text
)
returns public.refund_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := private.current_staff_id();
    result public.refund_requests%rowtype;
    appointment_row public.appointments%rowtype;
begin
    if actor_id is null or not private.has_permission('refund.manage') then
        raise exception using message = 'Refund rejection is not authorized', errcode = '42501';
    end if;
    if nullif(pg_catalog.btrim(p_reason), '') is null then
        raise exception using message = 'Refund rejection reason is required', errcode = '22023';
    end if;

    select * into result
      from public.refund_requests
     where id = p_refund_id
       and clinic_id = private.current_clinic_id()
     for update;
    if not found then
        raise exception using message = 'Refund request not found', errcode = 'P0002';
    end if;
    if result.status = 'rejected' and result.rejection_reason = pg_catalog.btrim(p_reason) then
        return result;
    end if;
    if result.status not in ('requested', 'under_review') then
        raise exception using message = 'Refund request cannot be rejected', errcode = 'check_violation';
    end if;

    update public.refund_requests
       set status = 'rejected',
           reviewed_by_staff_id = actor_id,
           reviewed_at = pg_catalog.now(),
           rejection_reason = pg_catalog.btrim(p_reason),
           approved_amount = null
     where id = result.id
    returning * into result;

    select * into appointment_row
      from public.appointments
     where id = result.appointment_id and clinic_id = result.clinic_id;

    insert into public.outbound_notifications (
        clinic_id, contact_id, booking_request_id, appointment_id,
        notification_type, event_key, template_data, scheduled_at
    ) values (
        result.clinic_id, appointment_row.contact_id,
        appointment_row.booking_request_id, appointment_row.id,
        'refund_rejected', 'refund_rejected:' || result.id::text,
        pg_catalog.jsonb_build_object(
            'refund_id', result.id,
            'appointment_reference', appointment_row.appointment_reference
        ),
        pg_catalog.now()
    )
    on conflict (clinic_id, event_key) where event_key is not null do nothing;

    return result;
end;
$$;

create or replace function public.record_refund_payout(
    p_refund_id uuid,
    p_payout_method text,
    p_payout_amount numeric,
    p_payout_reference text,
    p_proof_storage_path text,
    p_idempotency_key uuid
)
returns public.refund_requests
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := private.current_staff_id();
    result public.refund_requests%rowtype;
    payment_row public.payments%rowtype;
    appointment_row public.appointments%rowtype;
    normalized_method text := pg_catalog.lower(pg_catalog.btrim(p_payout_method));
    normalized_proof text := nullif(pg_catalog.btrim(p_proof_storage_path), '');
begin
    if actor_id is null or not private.has_permission('refund.manage') then
        raise exception using message = 'Refund payout is not authorized', errcode = '42501';
    end if;
    if p_idempotency_key is null
       or normalized_method is null
       or normalized_method not in ('cash', 'in_person', 'online')
       or p_payout_amount is null
       or p_payout_amount < 0 then
        raise exception using message = 'Valid refund payout details are required', errcode = '22023';
    end if;
    if normalized_method = 'online' and normalized_proof is null then
        raise exception using message = 'Online refund proof is required', errcode = '22023';
    end if;

    select * into result
      from public.refund_requests
     where id = p_refund_id
       and clinic_id = private.current_clinic_id()
     for update;
    if not found then
        raise exception using message = 'Refund request not found', errcode = 'P0002';
    end if;

    if result.status = 'refunded' then
        if result.payout_idempotency_key = p_idempotency_key
           and result.payout_method = normalized_method
           and result.payout_amount = p_payout_amount
           and result.payout_reference is not distinct from nullif(pg_catalog.btrim(p_payout_reference), '')
           and result.proof_storage_path is not distinct from normalized_proof then
            return result;
        end if;
        raise exception using message = 'Refund has already been paid', errcode = '23505';
    end if;

    if result.status <> 'approved_for_payout' then
        raise exception using message = 'Refund is not approved for payout', errcode = 'check_violation';
    end if;
    if p_payout_amount > result.approved_amount then
        raise exception using message = 'Payout exceeds the approved amount', errcode = 'check_violation';
    end if;
    if normalized_proof is not null
       and pg_catalog.split_part(normalized_proof, '/', 1) <> result.clinic_id::text then
        raise exception using message = 'Refund proof path must be clinic scoped', errcode = 'check_violation';
    end if;

    update public.refund_requests
       set status = 'refund_processing'
     where id = result.id;

    select * into payment_row
      from public.payments
     where id = result.payment_id
       and clinic_id = result.clinic_id
     for update;
    if payment_row.status <> 'verified' then
        raise exception using message = 'Linked payment is not eligible for payout', errcode = 'check_violation';
    end if;

    update public.payments set status = 'refund_required'
     where id = payment_row.id;
    update public.payments set status = 'refund_processing'
     where id = payment_row.id;
    update public.payments
       set status = 'refunded', refund_completed_at = pg_catalog.now()
     where id = payment_row.id;

    update public.refund_requests
       set status = 'refunded',
           payout_method = normalized_method,
           payout_amount = p_payout_amount,
           payout_reference = nullif(pg_catalog.btrim(p_payout_reference), ''),
           proof_storage_path = normalized_proof,
           paid_by_staff_id = actor_id,
           paid_at = pg_catalog.now(),
           payout_idempotency_key = p_idempotency_key
     where id = result.id
    returning * into result;

    select * into appointment_row
      from public.appointments
     where id = result.appointment_id and clinic_id = result.clinic_id;

    insert into public.outbound_notifications (
        clinic_id, contact_id, booking_request_id, appointment_id,
        notification_type, event_key, template_data, scheduled_at
    ) values (
        result.clinic_id, appointment_row.contact_id,
        appointment_row.booking_request_id, appointment_row.id,
        'refund_completed', 'refund_completed:' || result.id::text,
        pg_catalog.jsonb_build_object(
            'refund_id', result.id,
            'appointment_reference', appointment_row.appointment_reference,
            'payout_amount', result.payout_amount
        ),
        pg_catalog.now()
    )
    on conflict (clinic_id, event_key) where event_key is not null do nothing;

    return result;
end;
$$;

-- Check-in idempotency belongs to the visit because a confirmed appointment
-- has exactly one visit row.
alter table public.appointment_visits
    add column check_in_idempotency_key uuid;

create unique index appointment_visits_clinic_check_in_idempotency_unique
    on public.appointment_visits(clinic_id, check_in_idempotency_key)
    where check_in_idempotency_key is not null;

create or replace function public.check_in_appointment(
    p_appointment_id uuid,
    p_idempotency_key uuid,
    p_staff_user_id uuid,
    p_early_minutes integer,
    p_late_minutes integer
)
returns public.appointment_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
    staff_row public.staff_users%rowtype;
    appointment_row public.appointments%rowtype;
    request_row public.booking_requests%rowtype;
    payment_row public.payments%rowtype;
    service_requires_payment boolean;
    visit_row public.appointment_visits%rowtype;
begin
    if not private.is_service_role() then
        raise exception using message = 'Check-in requires the trusted server role', errcode = '42501';
    end if;
    if p_idempotency_key is null
       or p_staff_user_id is null
       or p_early_minutes is null or p_early_minutes not between 0 and 1440
       or p_late_minutes is null or p_late_minutes not between 0 and 1440 then
        raise exception using message = 'Valid server-owned check-in parameters are required', errcode = '22023';
    end if;

    select * into staff_row
      from public.staff_users
     where id = p_staff_user_id
       and active
       and role = 'receptionist';
    if not found or not private.staff_has_permission(staff_row.id, 'queue.update_all') then
        raise exception using message = 'Check-in is not authorized', errcode = '42501';
    end if;

    select a.*
      into appointment_row
      from public.appointments as a
     where a.id = p_appointment_id
       and a.clinic_id = staff_row.clinic_id
     for update of a;
    if not found then
        raise exception using message = 'Appointment not found', errcode = 'P0002';
    end if;

    select service.payment_required
      into service_requires_payment
      from public.services as service
     where service.id = appointment_row.service_id
       and service.clinic_id = appointment_row.clinic_id;
    if not found then
        raise exception using message = 'Appointment service not found', errcode = 'P0002';
    end if;

    select * into visit_row
      from public.appointment_visits
     where appointment_id = appointment_row.id
       and clinic_id = appointment_row.clinic_id
     for update;
    if found and visit_row.check_in_idempotency_key = p_idempotency_key then
        return visit_row;
    end if;

    if appointment_row.status <> 'confirmed' then
        raise exception using message = 'Only confirmed appointments can be checked in', errcode = 'check_violation';
    end if;

    if appointment_row.booking_request_id is not null then
        select * into request_row
          from public.booking_requests
         where id = appointment_row.booking_request_id
           and clinic_id = appointment_row.clinic_id;
    end if;
    if appointment_row.payment_id is not null then
        select * into payment_row
          from public.payments
         where id = appointment_row.payment_id
           and clinic_id = appointment_row.clinic_id;
    end if;

    if service_requires_payment
       and not pg_catalog.coalesce(request_row.payment_exempt, false)
       and pg_catalog.coalesce(payment_row.status, '') <> 'verified' then
        raise exception using message = 'Appointment payment is not verified', errcode = 'check_violation';
    end if;

    if pg_catalog.now() < appointment_row.start_at - pg_catalog.make_interval(mins => p_early_minutes)
       or pg_catalog.now() > appointment_row.start_at + pg_catalog.make_interval(mins => p_late_minutes) then
        raise exception using message = 'Appointment is outside the allowed check-in window', errcode = 'check_violation';
    end if;

    insert into public.appointment_visits (clinic_id, appointment_id)
    values (appointment_row.clinic_id, appointment_row.id)
    on conflict (appointment_id) do nothing;

    select * into visit_row
      from public.appointment_visits
     where appointment_id = appointment_row.id
       and clinic_id = appointment_row.clinic_id
     for update;

    if visit_row.queue_status <> 'scheduled' then
        raise exception using message = 'Visit is no longer awaiting check-in', errcode = '40001';
    end if;

    update public.appointment_visits
       set queue_status = 'checked_in',
           check_in_idempotency_key = p_idempotency_key,
           updated_by_staff_id = staff_row.id
     where id = visit_row.id
       and queue_status = 'scheduled'
    returning * into visit_row;
    if not found then
        raise exception using message = 'Visit changed concurrently', errcode = '40001';
    end if;

    insert into public.appointment_audit_log (
        clinic_id, appointment_id, patient_id, actor_type, actor_id,
        action, old_values, new_values
    ) values (
        appointment_row.clinic_id, appointment_row.id, appointment_row.patient_id,
        'receptionist', staff_row.id, 'queue_checked_in',
        pg_catalog.jsonb_build_object('queue_status', 'scheduled'),
        pg_catalog.jsonb_build_object('queue_status', 'checked_in')
    );

    return visit_row;
end;
$$;

-- Remove the legacy caller-supplied staff ID signature. The replacement
-- derives the authenticated actor and enforces role-specific transitions.
drop function public.transition_queue_state(uuid, text, text, uuid);

create or replace function public.transition_queue_state(
    p_visit_id uuid,
    p_expected_status text,
    p_target_status text
)
returns public.appointment_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
    actor_id uuid := private.current_staff_id();
    actor_role text := private.current_staff_role();
    visit_row public.appointment_visits%rowtype;
    appointment_row public.appointments%rowtype;
    doctor_is_authorized boolean := false;
begin
    if actor_id is null then
        raise exception using message = 'Queue actor is invalid', errcode = '42501';
    end if;

    select * into visit_row
      from public.appointment_visits
     where id = p_visit_id
       and clinic_id = private.current_clinic_id();
    if not found then
        raise exception using message = 'Visit not found', errcode = 'P0002';
    end if;

    select * into appointment_row
      from public.appointments
     where id = visit_row.appointment_id
       and clinic_id = visit_row.clinic_id
     for update;
    if not found then
        raise exception using message = 'Visit appointment not found', errcode = 'P0002';
    end if;

    select * into visit_row
      from public.appointment_visits
     where id = p_visit_id
       and clinic_id = appointment_row.clinic_id
       and appointment_id = appointment_row.id
     for update;
    if not found then
        raise exception using message = 'Visit changed concurrently', errcode = '40001';
    end if;

    if actor_role = 'receptionist' then
        if not private.has_permission('queue.update_all')
           or p_expected_status <> 'checked_in'
           or p_target_status <> 'waiting' then
            raise exception using message = 'Receptionist queue transition is not authorized', errcode = '42501';
        end if;
    elsif actor_role = 'doctor' then
        doctor_is_authorized := (
            private.has_permission('queue.update_all')
            or (
                private.has_permission('queue.update_own')
                and appointment_row.dentist_id = private.current_doctor_id()
            )
        );
        if not doctor_is_authorized
           or not (
               (p_expected_status = 'waiting' and p_target_status = 'in_progress')
               or (p_expected_status = 'in_progress' and p_target_status = 'completed')
           ) then
            raise exception using message = 'Doctor queue transition is not authorized', errcode = '42501';
        end if;
    else
        raise exception using message = 'Role cannot transition the clinical queue', errcode = '42501';
    end if;

    if visit_row.queue_status = p_target_status then
        return visit_row;
    end if;

    update public.appointment_visits
       set queue_status = p_target_status,
           updated_by_staff_id = actor_id
     where id = p_visit_id
       and queue_status = p_expected_status
    returning * into visit_row;
    if not found then
        raise exception using message = 'Visit changed concurrently', errcode = '40001';
    end if;

    if p_target_status = 'completed' then
        update public.appointments
           set status = 'completed', completed_by_staff_id = actor_id
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
        actor_role, actor_id,
        case p_target_status when 'in_progress' then 'queue_started' else 'queue_' || p_target_status end,
        pg_catalog.jsonb_build_object('queue_status', p_expected_status),
        pg_catalog.jsonb_build_object('queue_status', p_target_status)
    );

    return visit_row;
end;
$$;

revoke all on function public.request_refund(uuid, uuid, text, numeric, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.start_refund_review(uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.approve_refund(uuid, numeric)
    from public, anon, authenticated, service_role;
revoke all on function public.reject_refund(uuid, text)
    from public, anon, authenticated, service_role;
revoke all on function public.record_refund_payout(uuid, text, numeric, text, text, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.check_in_appointment(uuid, uuid, uuid, integer, integer)
    from public, anon, authenticated, service_role;
revoke all on function public.transition_queue_state(uuid, text, text)
    from public, anon, authenticated, service_role;

grant execute on function public.request_refund(uuid, uuid, text, numeric, uuid)
    to service_role;
grant execute on function public.start_refund_review(uuid)
    to authenticated;
grant execute on function public.approve_refund(uuid, numeric)
    to authenticated;
grant execute on function public.reject_refund(uuid, text)
    to authenticated;
grant execute on function public.record_refund_payout(uuid, text, numeric, text, text, uuid)
    to authenticated;
grant execute on function public.check_in_appointment(uuid, uuid, uuid, integer, integer)
    to service_role;
grant execute on function public.transition_queue_state(uuid, text, text)
    to authenticated;

insert into storage.buckets (id, name, public, file_size_limit)
values ('refund-proofs', 'refund-proofs', false, 10485760)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit;

create policy refund_proofs_authorized_read on storage.objects
for select to authenticated
using (
    bucket_id = 'refund-proofs'
    and (storage.foldername(name))[1] = (select private.current_clinic_id())::text
    and (select private.has_permission('refund.view_proof'))
);

create policy refund_proofs_authorized_insert on storage.objects
for insert to authenticated
with check (
    bucket_id = 'refund-proofs'
    and (storage.foldername(name))[1] = (select private.current_clinic_id())::text
    and (select private.has_permission('refund.view_proof'))
    and (select private.has_permission('refund.manage'))
);

create policy refund_proofs_authorized_update on storage.objects
for update to authenticated
using (
    bucket_id = 'refund-proofs'
    and (storage.foldername(name))[1] = (select private.current_clinic_id())::text
    and (select private.has_permission('refund.view_proof'))
    and (select private.has_permission('refund.manage'))
)
with check (
    bucket_id = 'refund-proofs'
    and (storage.foldername(name))[1] = (select private.current_clinic_id())::text
    and (select private.has_permission('refund.view_proof'))
    and (select private.has_permission('refund.manage'))
);

comment on column public.conversations.workflow_state is
    'Resumable non-secret patient workflow data; never credentials, tokens, API keys, or proof bytes.';
comment on column public.appointments.appointment_reference is
    'Patient-facing lookup identifier. It is not an authorization secret.';
comment on table public.refund_requests is
    'Clinic-scoped refund request, review, and payout lifecycle separate from appointment cancellation.';
comment on function public.check_in_appointment(uuid, uuid, uuid, integer, integer) is
    'Trusted-server-only check-in. FastAPI supplies bounded environment configuration for early/late tolerance; browser callers have no EXECUTE privilege.';

commit;
