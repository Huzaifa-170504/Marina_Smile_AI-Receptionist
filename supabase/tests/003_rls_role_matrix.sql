-- Executable RLS and RPC role matrix. Run after migrations and the demo seed.
-- All fixtures and mutations are rolled back.
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
    exists (select 1 from public.clinics where id = '10000000-0000-4000-8000-000000000001'),
    'Marina Smile seed must be applied first'
);

insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
    raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'db-owner-a@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'db-doctor-a@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'db-reception-a@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000004', 'authenticated', 'authenticated', 'db-verifier-a@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000005', 'authenticated', 'authenticated', 'db-inactive-a@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now()),
    ('00000000-0000-0000-0000-000000000000', 'a0000000-0000-4000-8000-000000000099', 'authenticated', 'authenticated', 'db-owner-b@example.invalid', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.clinics (id, name, slug)
values ('10000000-0000-4000-8000-000000000099', 'RLS Clinic B', 'rls-clinic-b');

insert into public.dentists (id, clinic_id, name)
values ('20000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', 'Dr. Clinic B');

insert into public.staff_users (
    id, clinic_id, auth_user_id, doctor_id, full_name, email, role, active
) values
    ('b0000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', null, 'Owner A', 'owner-a@example.invalid', 'owner', true),
    ('b0000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 'Doctor A', 'doctor-a@example.invalid', 'doctor', true),
    ('b0000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000003', null, 'Reception A', 'reception-a@example.invalid', 'receptionist', true),
    ('b0000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000004', null, 'Verifier A', 'verifier-a@example.invalid', 'payment_verifier', true),
    ('b0000000-0000-4000-8000-000000000005', '10000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000005', null, 'Inactive A', 'inactive-a@example.invalid', 'receptionist', false),
    ('b0000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', 'a0000000-0000-4000-8000-000000000099', null, 'Owner B', 'owner-b@example.invalid', 'owner', true);

insert into public.services (
    id, clinic_id, name, duration_minutes, price_from, payment_required
) values ('30000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', 'Clinic B Free Service', 30, 0, false);

insert into public.dentist_services (clinic_id, dentist_id, service_id) values
    ('10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000006'),
    ('10000000-0000-4000-8000-000000000099', '20000000-0000-4000-8000-000000000099', '30000000-0000-4000-8000-000000000099');

insert into public.contacts (id, clinic_id, whatsapp_id, name) values
    ('40000000-0000-4000-8000-000000000081', '10000000-0000-4000-8000-000000000001', 'rls-contact-a-own', 'Patient A Own'),
    ('40000000-0000-4000-8000-000000000082', '10000000-0000-4000-8000-000000000001', 'rls-contact-a-other', 'Patient A Other'),
    ('40000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', 'rls-contact-b', 'Patient B');

insert into public.patients (
    id, clinic_id, primary_contact_id, patient_number, full_name, age_at_registration, gender
) values
    ('41000000-0000-4000-8000-000000000081', '10000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000081', 'RLS-A-OWN', 'Patient A Own', 30, 'other'),
    ('41000000-0000-4000-8000-000000000082', '10000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000082', 'RLS-A-OTHER', 'Patient A Other', 31, 'other'),
    ('41000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', '40000000-0000-4000-8000-000000000099', 'RLS-B', 'Patient B', 32, 'other');

insert into public.booking_requests (
    id, clinic_id, patient_id, contact_id, service_id, preferred_dentist_id, status
) values
    ('61000000-0000-4000-8000-000000000081', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000081', '40000000-0000-4000-8000-000000000081', '30000000-0000-4000-8000-000000000006', '20000000-0000-4000-8000-000000000002', 'awaiting_slot_selection'),
    ('61000000-0000-4000-8000-000000000082', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000082', '40000000-0000-4000-8000-000000000082', '30000000-0000-4000-8000-000000000006', '20000000-0000-4000-8000-000000000001', 'awaiting_slot_selection'),
    ('61000000-0000-4000-8000-000000000083', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000081', '40000000-0000-4000-8000-000000000081', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 'payment_submitted'),
    ('61000000-0000-4000-8000-000000000084', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000082', '40000000-0000-4000-8000-000000000082', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 'awaiting_payment'),
    ('61000000-0000-4000-8000-000000000085', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000082', '40000000-0000-4000-8000-000000000082', '30000000-0000-4000-8000-000000000006', null, 'awaiting_patient_info'),
    ('61000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', '41000000-0000-4000-8000-000000000099', '40000000-0000-4000-8000-000000000099', '30000000-0000-4000-8000-000000000099', '20000000-0000-4000-8000-000000000099', 'awaiting_slot_selection');

insert into public.payments (
    id, clinic_id, booking_request_id, patient_id, amount_expected,
    proof_storage_path, status, submitted_at
) values (
    '62000000-0000-4000-8000-000000000083', '10000000-0000-4000-8000-000000000001',
    '61000000-0000-4000-8000-000000000083', '41000000-0000-4000-8000-000000000081',
    150, 'rls/a/payment.jpg', 'submitted', now()
);

insert into public.appointments (
    id, clinic_id, patient_id, contact_id, booking_request_id, service_id,
    dentist_id, start_at, end_at, idempotency_key
) values
    ('80000000-0000-4000-8000-000000000081', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000081', '40000000-0000-4000-8000-000000000081', '61000000-0000-4000-8000-000000000081', '30000000-0000-4000-8000-000000000006', '20000000-0000-4000-8000-000000000002', '2036-01-10 09:00+04', '2036-01-10 09:45+04', '71000000-0000-4000-8000-000000000081'),
    ('80000000-0000-4000-8000-000000000082', '10000000-0000-4000-8000-000000000001', '41000000-0000-4000-8000-000000000082', '40000000-0000-4000-8000-000000000082', '61000000-0000-4000-8000-000000000082', '30000000-0000-4000-8000-000000000006', '20000000-0000-4000-8000-000000000001', '2036-01-10 10:00+04', '2036-01-10 10:45+04', '71000000-0000-4000-8000-000000000082'),
    ('80000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', '41000000-0000-4000-8000-000000000099', '40000000-0000-4000-8000-000000000099', '61000000-0000-4000-8000-000000000099', '30000000-0000-4000-8000-000000000099', '20000000-0000-4000-8000-000000000099', '2036-01-10 09:00+04', '2036-01-10 09:30+04', '71000000-0000-4000-8000-000000000099');

update public.appointments
   set status = 'confirmed', google_calendar_event_id = 'gcal-' || id::text
 where id in (
    '80000000-0000-4000-8000-000000000081',
    '80000000-0000-4000-8000-000000000082',
    '80000000-0000-4000-8000-000000000099'
 );

-- OWNER: full access is limited to the owner's clinic; exemption is RPC-only.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 1 from public.clinics), 'owner sees only own clinic');
select pg_temp.assert_true((select count(*) = 2 from public.appointments), 'owner sees all own-clinic appointments');
select pg_temp.assert_true(
    (public.authorize_payment_exemption('61000000-0000-4000-8000-000000000084', 'Owner-approved courtesy', 'b0000000-0000-4000-8000-000000000001')).payment_exempt,
    'owner can authorize a payment exemption through the guarded RPC'
);
do $$
declare blocked boolean := false;
begin
    begin
        update public.booking_requests set payment_exempt = false
         where id = '61000000-0000-4000-8000-000000000084';
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'owner cannot mutate exemption fields directly');
end
$$;
reset role;

-- DOCTOR: assigned patients, appointments, and queue only.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000002","role":"authenticated"}', true);
select pg_temp.assert_true(
    (select count(*) = 1 and min(id) = '80000000-0000-4000-8000-000000000081' from public.appointments),
    'doctor sees only own appointment'
);
select pg_temp.assert_true(
    (select count(*) = 1 and min(id) = '41000000-0000-4000-8000-000000000081' from public.patients),
    'doctor sees only assigned patient'
);
select pg_temp.assert_true((select count(*) = 1 from public.appointment_visits), 'doctor sees only own queue');
do $$
declare blocked boolean := false;
begin
    begin
        perform public.verify_payment('62000000-0000-4000-8000-000000000083', 150, 'b0000000-0000-4000-8000-000000000002');
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'doctor cannot verify payments');
end
$$;
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_queue_state(
            (select id from public.appointment_visits where appointment_id = '80000000-0000-4000-8000-000000000081'),
            'scheduled', 'checked_in'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'doctor cannot perform front-desk check-in');
end
$$;
reset role;

-- RECEPTIONIST: operational visibility includes payment and refund operations.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000003","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 2 from public.appointments), 'receptionist sees all own-clinic appointments');
select pg_temp.assert_true((select count(*) = 2 from public.patients), 'receptionist sees own-clinic patients');
select pg_temp.assert_true((select count(*) = 1 from public.payments), 'receptionist can read own-clinic payments');
select pg_temp.assert_true(private.has_permission('payment.verify'), 'receptionist can verify payments');
select pg_temp.assert_true(private.has_permission('refund.manage'), 'receptionist can manage refunds');
update public.booking_requests set preferred_date = '2036-01-11'
 where id = '61000000-0000-4000-8000-000000000085';
select public.transition_booking_request(
    '61000000-0000-4000-8000-000000000085', 'awaiting_patient_info', 'awaiting_payment'
);
do $$
declare blocked boolean := false;
begin
    begin
        update public.booking_requests
           set patient_id = '41000000-0000-4000-8000-000000000081'
         where id = '61000000-0000-4000-8000-000000000085';
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'receptionist cannot change booking patient identity');
end
$$;
do $$
declare blocked boolean := false;
begin
    begin
        perform public.reserve_appointment(
            '10000000-0000-4000-8000-000000000001',
            '61000000-0000-4000-8000-000000000081', null,
            '20000000-0000-4000-8000-000000000002',
            '2036-01-11 09:00+04', '2036-01-11 09:45+04',
            '71000000-0000-4000-8000-000000000088'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'receptionist cannot execute server-only reservation');
end
$$;
reset role;

-- PAYMENT_VERIFIER: payment workflow and related patient only.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000004","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 1 from public.payments), 'verifier sees own-clinic payment');
select pg_temp.assert_true((select count(*) = 1 from public.patients), 'verifier sees payment-related patient');
select pg_temp.assert_true((select count(*) = 0 from public.appointments), 'verifier cannot read appointments');
select pg_temp.assert_true(
    (select transition_applied from public.verify_payment(
        '62000000-0000-4000-8000-000000000083', 150,
        'b0000000-0000-4000-8000-000000000004'
    )),
    'verifier can verify a submitted own-clinic payment'
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_booking_request(
            '61000000-0000-4000-8000-000000000099',
            'awaiting_slot_selection', 'cancelled'
        );
    exception when insufficient_privilege or serialization_failure then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'verifier cannot transition cross-clinic booking');
end
$$;
reset role;

-- INACTIVE STAFF: an auth identity without an active staff row has no tenant access.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000005","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 0 from public.clinics), 'inactive staff sees no clinic');
select pg_temp.assert_true((select count(*) = 0 from public.appointments), 'inactive staff sees no appointments');
select pg_temp.assert_true((select count(*) = 0 from public.patients), 'inactive staff sees no patients');
reset role;

-- CROSS-CLINIC: even an owner cannot see or mutate another clinic.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"a0000000-0000-4000-8000-000000000099","role":"authenticated"}', true);
select pg_temp.assert_true((select count(*) = 1 from public.clinics), 'clinic B owner sees one clinic');
select pg_temp.assert_true(
    (select count(*) = 1 and min(id) = '80000000-0000-4000-8000-000000000099' from public.appointments),
    'clinic B owner sees only clinic B appointment'
);
do $$
declare blocked boolean := false;
begin
    begin
        perform public.authorize_payment_exemption(
            '61000000-0000-4000-8000-000000000084', 'cross clinic attempt',
            'b0000000-0000-4000-8000-000000000099'
        );
    exception when no_data_found then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'clinic B owner cannot exempt clinic A request');
end
$$;
do $$
declare blocked boolean := false;
begin
    begin
        perform public.transition_booking_request(
            '61000000-0000-4000-8000-000000000085',
            'awaiting_payment', 'cancelled'
        );
    exception when serialization_failure then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'clinic B owner cannot transition clinic A booking');
end
$$;
reset role;

-- UNAUTHENTICATED: no public schema or table access is exposed.
set local role anon;
select pg_temp.assert_true(not has_schema_privilege('anon', 'public', 'usage'), 'anon has no public schema usage');
select pg_temp.assert_true(not has_table_privilege('anon', 'public.clinics', 'select'), 'anon has no clinic table access');
select pg_temp.assert_true(
    not has_function_privilege('anon', 'public.verify_payment(uuid,numeric,uuid)', 'execute'),
    'anon cannot execute payment verification'
);
reset role;

rollback;
