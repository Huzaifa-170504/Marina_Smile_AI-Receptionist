-- Run after all migrations and seed_demo_marina_smile.sql.
-- The transaction always rolls back its synthetic fixtures.
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
values ('10000000-0000-4000-8000-000000000099', 'Isolation Test Clinic', 'isolation-test');

insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
    raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
    '00000000-0000-0000-0000-000000000000',
    'a1000000-0000-4000-8000-000000000091',
    'authenticated', 'authenticated', 'integrity-owner@example.invalid', '', now(),
    '{"provider":"email","providers":["email"]}', '{}', now(), now()
);

insert into public.staff_users (
    id, clinic_id, auth_user_id, full_name, email, role
) values (
    '63000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    'a1000000-0000-4000-8000-000000000091',
    'Integrity Owner', 'integrity-owner-staff@example.invalid', 'owner'
);

insert into public.contacts (id, clinic_id, whatsapp_id, phone_number, name)
values (
    '40000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    'integrity-contact-1', '+971500000091', 'Integrity Patient'
);

insert into public.patients (
    id, clinic_id, primary_contact_id, patient_number, full_name,
    age_at_registration, gender
) values (
    '41000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    '40000000-0000-4000-8000-000000000091',
    'TEST-00091', 'Integrity Patient', 30, 'other'
);

insert into public.booking_requests (
    id, clinic_id, patient_id, contact_id, service_id, preferred_dentist_id, status
)
select
    request_id,
    '10000000-0000-4000-8000-000000000001'::uuid,
    '41000000-0000-4000-8000-000000000091'::uuid,
    '40000000-0000-4000-8000-000000000091'::uuid,
    '30000000-0000-4000-8000-000000000006'::uuid,
    '20000000-0000-4000-8000-000000000002'::uuid,
    case
        when request_id = '61000000-0000-4000-8000-000000000095'::uuid then 'awaiting_payment'
        else 'awaiting_slot_selection'
    end
from unnest(array[
    '61000000-0000-4000-8000-000000000091'::uuid,
    '61000000-0000-4000-8000-000000000092'::uuid,
    '61000000-0000-4000-8000-000000000093'::uuid,
    '61000000-0000-4000-8000-000000000094'::uuid,
    '61000000-0000-4000-8000-000000000095'::uuid
]) as requests(request_id);

insert into public.booking_requests (
    id, clinic_id, patient_id, contact_id, service_id, preferred_dentist_id, status
) values
    (
        '61000000-0000-4000-8000-000000000096',
        '10000000-0000-4000-8000-000000000001',
        '41000000-0000-4000-8000-000000000091',
        '40000000-0000-4000-8000-000000000091',
        '30000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000001',
        'awaiting_slot_selection'
    ),
    (
        '61000000-0000-4000-8000-000000000097',
        '10000000-0000-4000-8000-000000000001',
        '41000000-0000-4000-8000-000000000091',
        '40000000-0000-4000-8000-000000000091',
        '30000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000001',
        'payment_submitted'
    );

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.appointments (
            clinic_id, patient_id, contact_id, booking_request_id, service_id,
            dentist_id, start_at, end_at, status, idempotency_key
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '41000000-0000-4000-8000-000000000091',
            '40000000-0000-4000-8000-000000000091',
            '61000000-0000-4000-8000-000000000096',
            '30000000-0000-4000-8000-000000000001',
            '20000000-0000-4000-8000-000000000001',
            '2035-01-04T10:00:00+04:00', '2035-01-04T10:30:00+04:00',
            'pending', '71000000-0000-4000-8000-000000000096'
        );
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'payment-required appointment must not bypass clearance');
end
$$;

insert into public.payments (
    id, clinic_id, booking_request_id, patient_id, amount_expected,
    proof_storage_path, status, submitted_at
) values (
    '62000000-0000-4000-8000-000000000097',
    '10000000-0000-4000-8000-000000000001',
    '61000000-0000-4000-8000-000000000097',
    '41000000-0000-4000-8000-000000000091',
    150,
    '10000000-0000-4000-8000-000000000001/61000000-0000-4000-8000-000000000097/62000000-0000-4000-8000-000000000097/proof.jpg',
    'submitted', now()
);

do $$
declare blocked boolean := false;
begin
    begin
        perform public.verify_payment(
            '62000000-0000-4000-8000-000000000097', 150,
            '63000000-0000-4000-8000-000000000097'
        );
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'payment verification must require an authorized staff actor');
end
$$;

insert into public.appointments (
    id, clinic_id, patient_id, contact_id, booking_request_id, service_id,
    dentist_id, start_at, end_at, status, idempotency_key
) values (
    '80000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    '41000000-0000-4000-8000-000000000091',
    '40000000-0000-4000-8000-000000000091',
    '61000000-0000-4000-8000-000000000091',
    '30000000-0000-4000-8000-000000000006',
    '20000000-0000-4000-8000-000000000002',
    '2035-01-02T10:00:00+04:00', '2035-01-02T11:00:00+04:00',
    'pending', '71000000-0000-4000-8000-000000000091'
);

update public.appointments
   set status = 'confirmed', google_calendar_event_id = 'gcal-integrity-91'
 where id = '80000000-0000-4000-8000-000000000091';

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.appointments (
            clinic_id, patient_id, contact_id, booking_request_id, service_id,
            dentist_id, start_at, end_at, status, idempotency_key
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '41000000-0000-4000-8000-000000000091',
            '40000000-0000-4000-8000-000000000091',
            '61000000-0000-4000-8000-000000000092',
            '30000000-0000-4000-8000-000000000006',
            '20000000-0000-4000-8000-000000000002',
            '2035-01-02T10:30:00+04:00', '2035-01-02T11:30:00+04:00',
            'pending', '71000000-0000-4000-8000-000000000092'
        );
    exception when exclusion_violation then
        blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'overlapping active appointment must be blocked');
end
$$;

insert into public.appointments (
    clinic_id, patient_id, contact_id, booking_request_id, service_id,
    dentist_id, start_at, end_at, status, idempotency_key
) values (
    '10000000-0000-4000-8000-000000000001',
    '41000000-0000-4000-8000-000000000091',
    '40000000-0000-4000-8000-000000000091',
    '61000000-0000-4000-8000-000000000093',
    '30000000-0000-4000-8000-000000000006',
    '20000000-0000-4000-8000-000000000002',
    '2035-01-02T11:00:00+04:00', '2035-01-02T12:00:00+04:00',
    'pending', '71000000-0000-4000-8000-000000000093'
);

select pg_temp.assert_true(
    (select count(*) = 2 from public.appointments
      where dentist_id = '20000000-0000-4000-8000-000000000002'
        and start_at::date = '2035-01-02'),
    'adjacent half-open appointments must be allowed'
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.appointments (
            clinic_id, patient_id, contact_id, booking_request_id, service_id,
            dentist_id, start_at, end_at, status, idempotency_key
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '41000000-0000-4000-8000-000000000091',
            '40000000-0000-4000-8000-000000000091',
            '61000000-0000-4000-8000-000000000094',
            '30000000-0000-4000-8000-000000000006',
            '20000000-0000-4000-8000-000000000002',
            '2035-01-03T10:00:00+04:00', '2035-01-03T11:00:00+04:00',
            'pending', '71000000-0000-4000-8000-000000000091'
        );
    exception when unique_violation then
        blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'duplicate appointment idempotency key must be blocked');
end
$$;

insert into public.services (
    id, clinic_id, name, duration_minutes, price_from, payment_required
) values (
    '30000000-0000-4000-8000-000000000099',
    '10000000-0000-4000-8000-000000000099',
    'Isolation Service', 30, 0, false
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.dentist_services (clinic_id, dentist_id, service_id)
        values (
            '10000000-0000-4000-8000-000000000099',
            '20000000-0000-4000-8000-000000000001',
            '30000000-0000-4000-8000-000000000099'
        );
    exception when foreign_key_violation then
        blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'cross-clinic dentist/service link must be blocked');
end
$$;

insert into public.conversations (id, clinic_id, contact_id)
values (
    '42000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    '40000000-0000-4000-8000-000000000091'
);

insert into public.messages (
    clinic_id, conversation_id, contact_id, external_message_id, direction, role, text
) values (
    '10000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000091',
    '40000000-0000-4000-8000-000000000091',
    'wamid.integrity-91', 'inbound', 'user', 'test'
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.messages (
            clinic_id, conversation_id, contact_id, external_message_id, direction, role, text
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '42000000-0000-4000-8000-000000000091',
            '40000000-0000-4000-8000-000000000091',
            'wamid.integrity-91', 'inbound', 'user', 'duplicate'
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'duplicate external message ID must be blocked');
end
$$;

insert into public.contacts (id, clinic_id, whatsapp_id, name)
values (
    '40000000-0000-4000-8000-000000000092',
    '10000000-0000-4000-8000-000000000001',
    'integrity-contact-2', 'Different Contact'
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.messages (
            clinic_id, conversation_id, contact_id, external_message_id, direction, role, text
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '42000000-0000-4000-8000-000000000091',
            '40000000-0000-4000-8000-000000000092',
            'wamid.integrity-mismatch', 'inbound', 'user', 'wrong contact'
        );
    exception when foreign_key_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'message contact must match its conversation contact');
end
$$;

insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, notification_type,
    reminder_number, scheduled_at
) values (
    '10000000-0000-4000-8000-000000000001',
    '40000000-0000-4000-8000-000000000091',
    '61000000-0000-4000-8000-000000000095',
    'payment_reminder', 1, now()
);

do $$
declare blocked boolean := false;
begin
    begin
        insert into public.outbound_notifications (
            clinic_id, contact_id, booking_request_id, notification_type,
            reminder_number, scheduled_at
        ) values (
            '10000000-0000-4000-8000-000000000001',
            '40000000-0000-4000-8000-000000000091',
            '61000000-0000-4000-8000-000000000095',
            'payment_reminder', 1, now()
        );
    exception when unique_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'duplicate logical reminder must be blocked');
end
$$;

do $$
declare blocked boolean := false;
declare visit_id uuid;
begin
    select id into visit_id from public.appointment_visits
     where appointment_id = '80000000-0000-4000-8000-000000000091';
    begin
        update public.appointment_visits set queue_status = 'in_progress' where id = visit_id;
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'invalid queue transition must be blocked');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.appointments
           set status = 'completed'
         where id = '80000000-0000-4000-8000-000000000093';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'pending appointment cannot skip directly to completed');
end
$$;

select pg_temp.assert_true(
    (select count(*) >= 2 from public.appointment_audit_log
      where appointment_id = '80000000-0000-4000-8000-000000000091'),
    'appointment creation and confirmation must be audited'
);

do $$
declare blocked boolean := false;
declare audit_id uuid;
begin
    select id into audit_id
      from public.appointment_audit_log
     where appointment_id = '80000000-0000-4000-8000-000000000091'
     limit 1;
    begin
        update public.appointment_audit_log set reason = 'tampered' where id = audit_id;
    exception when insufficient_privilege then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'appointment audit rows must be append-only');
end
$$;

insert into public.appointment_change_requests (
    id, clinic_id, appointment_id, requested_by_actor_type, change_type,
    old_start_at, old_end_at, old_dentist_id, old_service_id,
    old_google_calendar_event_id
) values (
    '82000000-0000-4000-8000-000000000091',
    '10000000-0000-4000-8000-000000000001',
    '80000000-0000-4000-8000-000000000091',
    'system', 'reschedule',
    '2035-01-02T10:00:00+04:00', '2035-01-02T11:00:00+04:00',
    '20000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000006',
    'gcal-integrity-91'
);

do $$
declare blocked boolean := false;
begin
    begin
        update public.appointment_change_requests set status = 'applied'
         where id = '82000000-0000-4000-8000-000000000091';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'appointment change cannot skip acceptance');
end
$$;

update public.appointment_change_requests set status = 'accepted'
 where id = '82000000-0000-4000-8000-000000000091';
update public.appointment_change_requests set status = 'applied'
 where id = '82000000-0000-4000-8000-000000000091';
select pg_temp.assert_true(
    (select resolved_at is not null from public.appointment_change_requests
      where id = '82000000-0000-4000-8000-000000000091'),
    'applied appointment change must record resolution time'
);

insert into public.doctor_status_change_requests (
    id, clinic_id, dentist_id, requested_by_staff_id, requested_status, reason
) values
    ('83000000-0000-4000-8000-000000000091', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000003', '63000000-0000-4000-8000-000000000091', 'inactive', 'Planned departure'),
    ('83000000-0000-4000-8000-000000000092', '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', '63000000-0000-4000-8000-000000000091', 'inactive', 'Future appointment guard');

update public.doctor_status_change_requests
   set status = 'approved',
       reviewed_by_staff_id = '63000000-0000-4000-8000-000000000091',
       reviewed_at = now()
 where id in (
    '83000000-0000-4000-8000-000000000091',
    '83000000-0000-4000-8000-000000000092'
 );

update public.dentists
   set active = false,
       deactivated_by_staff_id = '63000000-0000-4000-8000-000000000091',
       deactivation_reason = 'Planned departure'
 where id = '20000000-0000-4000-8000-000000000003';

select pg_temp.assert_true(
    (select status = 'applied' from public.doctor_status_change_requests
      where id = '83000000-0000-4000-8000-000000000091'),
    'doctor deactivation must atomically apply its approved request'
);

do $$
declare blocked boolean := false;
begin
    begin
        update public.dentists
           set active = false,
               deactivated_by_staff_id = '63000000-0000-4000-8000-000000000091',
               deactivation_reason = 'Future appointment guard'
         where id = '20000000-0000-4000-8000-000000000002';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'dentist with future active appointments cannot be deactivated');
end
$$;

do $$
declare blocked boolean := false;
begin
    begin
        update public.dentists set active = true
         where id = '20000000-0000-4000-8000-000000000003';
    exception when check_violation then blocked := true;
    end;
    perform pg_temp.assert_true(blocked, 'doctor reactivation requires a future governed workflow');
end
$$;

select pg_temp.assert_true(
    exists (
        select 1 from public.appointments
         where dentist_id = '20000000-0000-4000-8000-000000000002'
           and start_at > now()
           and status in ('pending', 'confirmed')
    ),
    'future active dentist appointment lookup must be supported'
);

select pg_temp.assert_true(
    (
        select format_type(a.atttypid, a.atttypmod) = 'vector(768)'
          from pg_attribute a
          join pg_class c on c.oid = a.attrelid
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relname = 'knowledge_chunks'
           and a.attname = 'embedding' and not a.attisdropped
    ),
    'RAG embedding must be vector(768)'
);

insert into public.knowledge_documents (
    id, clinic_id, filename, checksum, status
) values
    ('90000000-0000-4000-8000-000000000091', '10000000-0000-4000-8000-000000000001', 'clinic-one.pdf', repeat('a', 64), 'ready'),
    ('90000000-0000-4000-8000-000000000099', '10000000-0000-4000-8000-000000000099', 'clinic-two.pdf', repeat('b', 64), 'ready');

insert into public.knowledge_chunks (
    clinic_id, document_id, content, embedding, chunk_index
)
select clinic_id, document_id, content,
       array_prepend(1::real, array_fill(0::real, array[767]))::extensions.vector(768), 0
from (values
    ('10000000-0000-4000-8000-000000000001'::uuid, '90000000-0000-4000-8000-000000000091'::uuid, 'clinic one'),
    ('10000000-0000-4000-8000-000000000099'::uuid, '90000000-0000-4000-8000-000000000099'::uuid, 'clinic two')
) as fixture(clinic_id, document_id, content);

select pg_temp.assert_true(
    (select count(*) = 1 and min(filename) = 'clinic-one.pdf'
       from public.match_knowledge_chunks(
           '10000000-0000-4000-8000-000000000001',
           array_prepend(1::real, array_fill(0::real, array[767]))::extensions.vector(768),
           0, 10
       )),
    'RAG RPC must filter by clinic before ranking'
);

update public.booking_requests
   set status = 'expired'
 where id = '61000000-0000-4000-8000-000000000095';

select pg_temp.assert_true(
    exists (select 1 from public.patients where id = '41000000-0000-4000-8000-000000000091'),
    'expiring an unpaid request must not delete patient history'
);

rollback;
