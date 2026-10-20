-- Patient operations foundation: schema, idempotency, refund, RLS, check-in,
-- and queue authorization. Run after all migrations and the demo seed.
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

select pg_temp.assert_true(
    exists (select 1 from public.clinics where id = '10000000-0000-4000-8000-000000000001'),
    'Marina Smile seed must be applied first'
);

insert into public.clinics (id, name, slug)
values ('10000000-0000-4000-8000-000000000098', 'Patient Ops Isolation', 'patient-ops-isolation');

insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
    raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
    ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'ops-reception@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'ops-doctor@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'ops-verifier@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000004', 'authenticated', 'authenticated', 'ops-owner@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000098', 'authenticated', 'authenticated', 'ops-owner-b@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.dentists (id, clinic_id, name)
values ('20000000-0000-4000-8000-000000000098', '10000000-0000-4000-8000-000000000098', 'Dr. Isolation');

insert into public.staff_users (
    id, clinic_id, auth_user_id, doctor_id, full_name, email, role
) values
    ('c0000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001', null, 'Ops Reception', 'ops-reception-staff@example.invalid', 'receptionist'),
    ('c0000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000001', 'Ops Doctor', 'ops-doctor-staff@example.invalid', 'doctor'),
    ('c0000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000003', null, 'Ops Verifier', 'ops-verifier-staff@example.invalid', 'payment_verifier'),
    ('c0000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000004', null, 'Ops Owner', 'ops-owner-staff@example.invalid', 'owner'),
    ('c0000000-0000-4000-8000-000000000098', '10000000-0000-4000-8000-000000000098', 'd0000000-0000-4000-8000-000000000098', null, 'Ops Owner B', 'ops-owner-b-staff@example.invalid', 'owner');

insert into public.services (
    id, clinic_id, name, duration_minutes, price_from, payment_required
) values (
    '30000000-0000-4000-8000-000000000098',
    '10000000-0000-4000-8000-000000000098',
    'Isolation Consultation', 30, 100, true
);

insert into public.dentist_services (clinic_id, dentist_id, service_id)
values (
    '10000000-0000-4000-8000-000000000098',
    '20000000-0000-4000-8000-000000000098',
    '30000000-0000-4000-8000-000000000098'
);

insert into public.contacts (id, clinic_id, whatsapp_id, phone_number, name) values
    ('40000000-0000-4000-8000-000000000071', '10000000-0000-4000-8000-000000000001', 'ops-contact-a', '+971500000071', 'Ops Patient A'),
    ('40000000-0000-4000-8000-000000000098', '10000000-0000-4000-8000-000000000098', 'ops-contact-b', '+971500000098', 'Ops Patient B');

insert into public.patients (
    id, clinic_id, primary_contact_id, patient_number, full_name,
    age_at_registration, gender
) values
    ('41000000-0000-4000-8000-000000000071', '10000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000071', 'OPS-A-071', 'Ops Patient A', 30, 'other'),
    ('41000000-0000-4000-8000-000000000098', '10000000-0000-4000-8000-000000000098', '40000000-0000-4000-8000-000000000098', 'OPS-B-098', 'Ops Patient B', 31, 'other');

-- Workflow constraints and clinic-consistent active booking linkage.
insert into public.booking_requests (
    id, clinic_id, patient_id, contact_id, service_id, status, idempotency_key
) values
    ('61000000-0000-4000-8000-000000000071', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000071', '40000000-0000-4000-8000-000000000071', '30000000-0000-4000-8000-000000000001', 'awaiting_payment', '71000000-0000-4000-8000-000000000071'),
    ('61000000-0000-4000-8000-000000000098', '10000000-0000-4000-8000-000000000098', '41000000-0000-4000-8000-000000000098', '40000000-0000-4000-8000-000000000098', '30000000-0000-4000-8000-000000000098', 'awaiting_payment', '71000000-0000-4000-8000-000000000071');

insert into public.conversations (
    id, clinic_id, contact_id, workflow_mode, workflow_step,
    workflow_state, active_booking_request_id
) values (
    '42000000-0000-4000-8000-000000000071',
    '10000000-0000-4000-8000-000000000001',
    '40000000-0000-4000-8000-000000000071',
    'BOOKING', 'payment_instructions', '{"service_confirmed":true}',
    '61000000-0000-4000-8000-000000000071'
);

do $$
declare blocked boolean := false;
begin
    begin
        update public.conversations
           set workflow_mode = 'UNTRUSTED_MODE'
         where id = '42000000-0000-4000-8000-000000000071';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'unknown workflow mode must be rejected');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.conversations
           set workflow_state = '[]'::jsonb
         where id = '42000000-0000-4000-8000-000000000071';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'workflow state must remain a JSON object');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.conversations
           set active_booking_request_id = '61000000-0000-4000-8000-000000000098'
         where id = '42000000-0000-4000-8000-000000000071';
    exception when foreign_key_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'active booking request must belong to the conversation clinic');
end
$$;

-- Booking, payment-submission, and notification logical idempotency.
do $$
declare blocked boolean := false;
begin
    begin
        insert into public.booking_requests (
            clinic_id, patient_id, contact_id, service_id, status, idempotency_key
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '41000000-0000-4000-8000-000000000071',
            '40000000-0000-4000-8000-000000000071',
            '30000000-0000-4000-8000-000000000001',
            'awaiting_payment',
            '71000000-0000-4000-8000-000000000071'
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'booking idempotency must be unique within a clinic');
end
$$;

insert into public.payments (
    id, clinic_id, booking_request_id, patient_id, amount_expected,
    status, submission_idempotency_key
) values (
    '62000000-0000-4000-8000-000000000071',
    '10000000-0000-4000-8000-000000000001',
    '61000000-0000-4000-8000-000000000071',
    '41000000-0000-4000-8000-000000000071',
    150, 'awaiting', '72000000-0000-4000-8000-000000000071'
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.payments (
            clinic_id, booking_request_id, patient_id, amount_expected,
            status, submission_idempotency_key
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '61000000-0000-4000-8000-000000000071',
            '41000000-0000-4000-8000-000000000071',
            150, 'awaiting', '72000000-0000-4000-8000-000000000071'
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'payment submission idempotency must be unique within a clinic');
end
$$;

insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, notification_type,
    event_key, template_data, scheduled_at
) values (
    '10000000-0000-4000-8000-000000000001',
    '40000000-0000-4000-8000-000000000071',
    '61000000-0000-4000-8000-000000000071',
    'payment_verified',
    'payment_verified:62000000-0000-4000-8000-000000000071',
    '{"safe":true}', now()
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.outbound_notifications (
            clinic_id, contact_id, booking_request_id, notification_type,
            event_key, scheduled_at
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '40000000-0000-4000-8000-000000000071',
            '61000000-0000-4000-8000-000000000071',
            'payment_verified',
            'payment_verified:62000000-0000-4000-8000-000000000071',
            now()
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'notification event key must prevent duplicate logical events');
end
$$;

-- Helper used only by this rolled-back test transaction.
create or replace function pg_temp.create_paid_confirmed_appointment(
    p_booking_id uuid,
    p_payment_id uuid,
    p_appointment_id uuid,
    p_dentist_id uuid,
    p_start_at timestamptz,
    p_idempotency_key uuid
)
returns void
language plpgsql
as $$
begin
    insert into public.booking_requests (
        id, clinic_id, patient_id, contact_id, service_id,
        preferred_dentist_id, status
    ) values (
        p_booking_id,
        '10000000-0000-4000-8000-000000000001',
        '41000000-0000-4000-8000-000000000071',
        '40000000-0000-4000-8000-000000000071',
        '30000000-0000-4000-8000-000000000001',
        p_dentist_id, 'awaiting_slot_selection'
    );

    insert into public.payments (
        id, clinic_id, booking_request_id, patient_id,
        amount_expected, amount_verified, status,
        verified_by_staff_id, verified_at
    ) values (
        p_payment_id,
        '10000000-0000-4000-8000-000000000001',
        p_booking_id,
        '41000000-0000-4000-8000-000000000071',
        150, 150, 'verified',
        'c0000000-0000-4000-8000-000000000001', now()
    );

    insert into public.appointments (
        id, clinic_id, patient_id, contact_id, booking_request_id, payment_id,
        service_id, dentist_id, start_at, end_at, status, idempotency_key
    ) values (
        p_appointment_id,
        '10000000-0000-4000-8000-000000000001',
        '41000000-0000-4000-8000-000000000071',
        '40000000-0000-4000-8000-000000000071',
        p_booking_id, p_payment_id,
        '30000000-0000-4000-8000-000000000001', p_dentist_id,
        p_start_at, p_start_at + interval '30 minutes',
        'pending', p_idempotency_key
    );

    update public.appointments
       set status = 'confirmed', google_calendar_event_id = 'gcal-' || p_appointment_id::text
     where id = p_appointment_id;
end;
$$;

select pg_temp.create_paid_confirmed_appointment(
    '61000000-0000-4000-8000-000000000072',
    '62000000-0000-4000-8000-000000000072',
    '80000000-0000-4000-8000-000000000072',
    '20000000-0000-4000-8000-000000000001',
    now() + interval '2 days',
    '73000000-0000-4000-8000-000000000072'
);

select pg_temp.assert_true(
    not exists (select 1 from public.appointments where appointment_reference is null)
    and not exists (
        select 1 from public.appointments
         where appointment_reference !~ '^MS-APT-[0-9]{8}-[0-9]{6,}$'
    )
    and (
        select count(*) = count(distinct appointment_reference)
          from public.appointments
    ),
    'existing and new appointments must have unique human-readable references'
);

do $$
declare blocked boolean := false;
begin
    begin
        update public.appointments
           set appointment_reference = 'MS-APT-20990101-999999'
         where id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'appointment reference must be server-owned and immutable');
end
$$;

-- Refund fixture: verified payment plus a cancelled appointment.
update public.appointments
   set status = 'cancelled', cancelled_at = now(),
       cancellation_reason = 'Patient request', cancellation_source = 'patient'
 where id = '80000000-0000-4000-8000-000000000072';

set local role service_role;
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select public.request_refund(
    '80000000-0000-4000-8000-000000000072',
    '62000000-0000-4000-8000-000000000072',
    'Cancelled visit', 150,
    '74000000-0000-4000-8000-000000000072'
);
select public.request_refund(
    '80000000-0000-4000-8000-000000000072',
    '62000000-0000-4000-8000-000000000072',
    'Cancelled visit', 150,
    '74000000-0000-4000-8000-000000000072'
);
reset role;

select pg_temp.assert_true(
    (select count(*) = 1 from public.refund_requests
      where appointment_id = '80000000-0000-4000-8000-000000000072'),
    'refund request replay must not create a duplicate'
);

do $$
declare blocked boolean := false;
begin
    begin
        update public.refund_requests
           set approved_amount = eligible_amount + 1
         where appointment_id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'approved refund cannot exceed eligible amount');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.refund_requests
           set status = 'approved_for_payout', approved_amount = 150
         where appointment_id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'approval requires reviewer and review timestamp');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.refund_requests
           set status = 'rejected', rejection_reason = 'Incomplete request'
         where appointment_id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'rejection requires reviewer and review timestamp');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.refund_requests
           set status = 'rejected', rejection_reason = 'Incomplete request'
         where appointment_id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'rejection requires reviewer and review timestamp');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.refund_requests
           set patient_id = '41000000-0000-4000-8000-000000000098'
         where appointment_id = '80000000-0000-4000-8000-000000000072';
    exception when check_violation or foreign_key_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'refund links cannot cross patients or clinics');
end
$$;

-- Permission defaults and refund RLS.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select pg_temp.assert_true(private.has_permission('payment.verify'), 'receptionist has payment verification permission');
select pg_temp.assert_true(private.has_permission('payment.view_proof'), 'receptionist has payment proof permission');
select pg_temp.assert_true(private.has_permission('refund.manage'), 'receptionist has refund management permission');
select pg_temp.assert_true(private.has_permission('refund.view_proof'), 'receptionist has refund proof permission');
select pg_temp.assert_true((select count(*) = 1 from public.refund_requests), 'receptionist sees own-clinic refunds');
select public.start_refund_review((select id from public.refund_requests limit 1));
select public.approve_refund((select id from public.refund_requests limit 1), 150);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.record_refund_payout(
            (select id from public.refund_requests limit 1),
            'online', 150, 'bank-ref-1', null,
            '75000000-0000-4000-8000-000000000072'
        );
    exception when invalid_parameter_value then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'online payout must require proof');
end
$$;
select public.record_refund_payout(
    (select id from public.refund_requests limit 1),
    'online', 150, 'bank-ref-1',
    '10000000-0000-4000-8000-000000000001/refunds/refund-72/proof.jpg',
    '75000000-0000-4000-8000-000000000072'
);
select public.record_refund_payout(
    (select id from public.refund_requests limit 1),
    'online', 150, 'bank-ref-1',
    '10000000-0000-4000-8000-000000000001/refunds/refund-72/proof.jpg',
    '75000000-0000-4000-8000-000000000072'
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.record_refund_payout(
            (select id from public.refund_requests limit 1),
            'online', 150, 'bank-ref-2',
            '10000000-0000-4000-8000-000000000001/refunds/refund-72/other.jpg',
            '75000000-0000-4000-8000-000000000073'
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'a completed refund cannot be paid twice');
end
$$;
reset role;

select pg_temp.assert_true(
    (select status = 'refunded' and paid_by_staff_id = 'c0000000-0000-4000-8000-000000000001'
       from public.refund_requests
      where appointment_id = '80000000-0000-4000-8000-000000000072'),
    'successful payout records the authenticated receptionist exactly once'
);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000002","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 0 from public.refund_requests), 'doctor cannot see refund queue');
select pg_temp.assert_true(not private.has_permission('refund.manage'), 'doctor has no refund management permission');
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000003","role":"authenticated"}', true);
select pg_temp.assert_true(private.has_permission('payment.verify'), 'payment verifier retains payment permission');
select pg_temp.assert_true(not private.has_permission('refund.manage'), 'payment verifier has no default refund permission');
select pg_temp.assert_true((select count(*) = 0 from public.refund_requests), 'payment verifier cannot see refund queue');
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000004","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 1 from public.refund_requests), 'owner sees own-clinic refund history');
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000098","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 0 from public.refund_requests), 'other-clinic owner cannot see refund history');
reset role;

-- Check-in fixtures use server time and bounded tolerance supplied only by the
-- trusted backend RPC. Browser/authenticated roles have no EXECUTE privilege.
select pg_temp.create_paid_confirmed_appointment(
    '61000000-0000-4000-8000-000000000073',
    '62000000-0000-4000-8000-000000000073',
    '80000000-0000-4000-8000-000000000073',
    '20000000-0000-4000-8000-000000000001',
    now() + interval '5 minutes',
    '73000000-0000-4000-8000-000000000073'
);
select pg_temp.create_paid_confirmed_appointment(
    '61000000-0000-4000-8000-000000000074',
    '62000000-0000-4000-8000-000000000074',
    '80000000-0000-4000-8000-000000000074',
    '20000000-0000-4000-8000-000000000002',
    now() + interval '3 hours',
    '73000000-0000-4000-8000-000000000074'
);
select pg_temp.create_paid_confirmed_appointment(
    '61000000-0000-4000-8000-000000000075',
    '62000000-0000-4000-8000-000000000075',
    '80000000-0000-4000-8000-000000000075',
    '20000000-0000-4000-8000-000000000003',
    now() - interval '3 hours',
    '73000000-0000-4000-8000-000000000075'
);
select pg_temp.create_paid_confirmed_appointment(
    '61000000-0000-4000-8000-000000000076',
    '62000000-0000-4000-8000-000000000076',
    '80000000-0000-4000-8000-000000000076',
    '20000000-0000-4000-8000-000000000002',
    now() + interval '15 minutes',
    '73000000-0000-4000-8000-000000000076'
);
update public.payments
   set status = 'refund_required'
 where id = '62000000-0000-4000-8000-000000000076';

set local role service_role;
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select public.check_in_appointment(
    '80000000-0000-4000-8000-000000000073',
    '76000000-0000-4000-8000-000000000073',
    'c0000000-0000-4000-8000-000000000001', 30, 30
);
select public.check_in_appointment(
    '80000000-0000-4000-8000-000000000073',
    '76000000-0000-4000-8000-000000000073',
    'c0000000-0000-4000-8000-000000000001', 30, 30
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.check_in_appointment(
            '80000000-0000-4000-8000-000000000074',
            '76000000-0000-4000-8000-000000000074',
            'c0000000-0000-4000-8000-000000000001', 30, 30
        );
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'too-early check-in must be denied');
end
$$;
do $$
declare blocked boolean := false;
begin
    begin
        perform public.check_in_appointment(
            '80000000-0000-4000-8000-000000000075',
            '76000000-0000-4000-8000-000000000075',
            'c0000000-0000-4000-8000-000000000001', 30, 30
        );
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'too-late check-in must be denied');
end
$$;
do $$
declare blocked boolean := false;
begin
    begin
        perform public.check_in_appointment(
            '80000000-0000-4000-8000-000000000076',
            '76000000-0000-4000-8000-000000000076',
            'c0000000-0000-4000-8000-000000000001', 30, 30
        );
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'unverified payment must deny check-in');
end
$$;
reset role;

select pg_temp.assert_true(
    (select queue_status = 'checked_in'
       from public.appointment_visits
      where appointment_id = '80000000-0000-4000-8000-000000000073'),
    'valid check-in moves scheduled visit to checked_in exactly once'
);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select public.transition_queue_state(
    (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000073'),
    'checked_in', 'waiting'
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_queue_state(
            (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000073'),
            'waiting', 'in_progress'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'receptionist cannot start treatment');
end
$$;
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000004","role":"authenticated"}', true);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_queue_state(
            (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000073'),
            'waiting', 'in_progress'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'owner has visibility but no invented treatment override');
end
$$;
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"d0000000-0000-4000-8000-000000000002","role":"authenticated"}', true);
select public.transition_queue_state(
    (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000073'),
    'waiting', 'in_progress'
);
select public.transition_queue_state(
    (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000073'),
    'in_progress', 'completed'
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_queue_state(
            (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000074'),
            'scheduled', 'checked_in'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'doctor cannot perform receptionist check-in');
end
$$;
reset role;

select pg_temp.assert_true(
    (select queue_status = 'completed'
       from public.appointment_visits
      where appointment_id = '80000000-0000-4000-8000-000000000073')
    and
    (select status = 'completed'
       from public.appointments
      where id = '80000000-0000-4000-8000-000000000073'),
    'doctor treatment completion atomically completes visit and appointment'
);

rollback;
