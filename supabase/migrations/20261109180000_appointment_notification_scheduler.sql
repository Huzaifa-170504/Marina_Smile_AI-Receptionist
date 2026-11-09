begin;

-- The appointment row is the authoritative source for reminder timing.
-- This trigger guarantees that every confirmed appointment has 24-hour and
-- 2-hour reminders addressed to its own contact, that rescheduling moves the
-- pending reminders to the new start time, and that cancellation/no-show/
-- completion removes future reminders. Feedback requests are handled by
-- private.schedule_feedback_request().
create or replace function private.schedule_appointment_notifications()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    reminder record;
    existing_id uuid;
    next_number integer;
    scheduled_at timestamptz;
    template jsonb;
begin
    if new.status = 'confirmed'
       and (
           tg_op = 'INSERT'
           or old.status is distinct from 'confirmed'
           or old.start_at is distinct from new.start_at
       ) then
        template := pg_catalog.jsonb_build_object(
            'appointment_reference', new.appointment_reference,
            'start_at', new.start_at,
            'payment_mode', new.payment_mode,
            'payment_status', new.payment_status
        );

        for reminder in
            select *
              from (values
                  ('appointment_reminder', pg_catalog.make_interval(hours => 24)),
                  ('final_reminder', pg_catalog.make_interval(hours => 2))
              ) as entries(notification_type, lead_time)
        loop
            scheduled_at := new.start_at - reminder.lead_time;
            if scheduled_at <= pg_catalog.now() then
                scheduled_at := pg_catalog.now() + pg_catalog.make_interval(mins => 1);
            end if;

            existing_id := null;
            select id into existing_id
              from public.outbound_notifications
             where clinic_id = new.clinic_id
               and appointment_id = new.id
               and notification_type = reminder.notification_type
               and status = 'pending'
             order by created_at
             limit 1;

            if existing_id is not null then
                update public.outbound_notifications
                   set scheduled_at = scheduled_at,
                       template_data = template
                 where id = existing_id
                   and status = 'pending';
            else
                select coalesce(max(reminder_number), 0) + 1 into next_number
                  from public.outbound_notifications
                 where clinic_id = new.clinic_id
                   and appointment_id = new.id
                   and notification_type = reminder.notification_type;

                insert into public.outbound_notifications (
                    clinic_id, contact_id, booking_request_id, appointment_id,
                    notification_type, channel, reminder_number, scheduled_at,
                    event_key, template_data
                ) values (
                    new.clinic_id, new.contact_id, new.booking_request_id, new.id,
                    reminder.notification_type, 'telegram', next_number, scheduled_at,
                    reminder.notification_type || ':' || new.id::text || ':' ||
                        pg_catalog.gen_random_uuid()::text,
                    template
                )
                on conflict do nothing;
            end if;
        end loop;
    end if;

    if tg_op = 'UPDATE'
       and old.status is distinct from new.status
       and new.status in ('cancelled', 'no_show', 'completed') then
        update public.outbound_notifications
           set status = 'cancelled'
         where clinic_id = new.clinic_id
           and appointment_id = new.id
           and status in ('pending', 'processing')
           and (
               new.status <> 'completed'
               or notification_type in ('appointment_reminder', 'final_reminder')
           );
    end if;

    return new;
end;
$$;

revoke all on function private.schedule_appointment_notifications()
    from public, anon, authenticated, service_role;

drop trigger if exists appointments_schedule_notifications on public.appointments;
create trigger appointments_schedule_notifications
after insert or update of status, start_at on public.appointments
for each row execute function private.schedule_appointment_notifications();

-- Backfill reminders for confirmed appointments that are still in the future.
insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, appointment_id,
    notification_type, channel, reminder_number, scheduled_at,
    event_key, template_data
)
select
    a.clinic_id, a.contact_id, a.booking_request_id, a.id,
    'appointment_reminder', 'telegram', 1,
    greatest(
        a.start_at - pg_catalog.make_interval(hours => 24),
        pg_catalog.now() + pg_catalog.make_interval(mins => 1)
    ),
    'appointment_reminder:' || a.id::text || ':' || pg_catalog.gen_random_uuid()::text,
    pg_catalog.jsonb_build_object(
        'appointment_reference', a.appointment_reference,
        'start_at', a.start_at,
        'payment_mode', a.payment_mode,
        'payment_status', a.payment_status
    )
from public.appointments as a
where a.status = 'confirmed'
  and a.start_at > pg_catalog.now()
on conflict do nothing;

insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, appointment_id,
    notification_type, channel, reminder_number, scheduled_at,
    event_key, template_data
)
select
    a.clinic_id, a.contact_id, a.booking_request_id, a.id,
    'final_reminder', 'telegram', 1,
    greatest(
        a.start_at - pg_catalog.make_interval(hours => 2),
        pg_catalog.now() + pg_catalog.make_interval(mins => 1)
    ),
    'final_reminder:' || a.id::text || ':' || pg_catalog.gen_random_uuid()::text,
    pg_catalog.jsonb_build_object(
        'appointment_reference', a.appointment_reference,
        'start_at', a.start_at,
        'payment_mode', a.payment_mode,
        'payment_status', a.payment_status
    )
from public.appointments as a
where a.status = 'confirmed'
  and a.start_at > pg_catalog.now()
on conflict do nothing;

commit;
