begin;

-- Reminder policy:
--   * more than 24 hours away -> 24-hour reminder + 2-hour reminder
--   * within 24 hours        -> only the 2-hour reminder
--   * within 2 hours         -> only the 2-hour reminder (sent as soon as possible)
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
    within_twenty_four_hours boolean;
begin
    if new.status = 'confirmed'
       and (
           tg_op = 'INSERT'
           or old.status is distinct from 'confirmed'
           or old.start_at is distinct from new.start_at
       ) then
        within_twenty_four_hours :=
            (new.start_at - pg_catalog.now()) <= pg_catalog.make_interval(hours => 24);

        template := pg_catalog.jsonb_build_object(
            'appointment_reference', new.appointment_reference,
            'start_at', new.start_at,
            'payment_mode', new.payment_mode,
            'payment_status', new.payment_status
        );

        -- A 24-hour reminder is no longer applicable inside 24 hours.
        if within_twenty_four_hours then
            update public.outbound_notifications
               set status = 'cancelled'
             where clinic_id = new.clinic_id
               and appointment_id = new.id
               and notification_type = 'appointment_reminder'
               and status in ('pending', 'processing');
        end if;

        for reminder in
            select *
              from (values
                  (
                      'appointment_reminder',
                      pg_catalog.make_interval(hours => 24),
                      not within_twenty_four_hours
                  ),
                  (
                      'final_reminder',
                      pg_catalog.make_interval(hours => 2),
                      true
                  )
              ) as entries(notification_type, lead_time, should_schedule)
        loop
            if not reminder.should_schedule then
                continue;
            end if;

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

-- Remove 24-hour reminders that are no longer applicable.
update public.outbound_notifications as notification
   set status = 'cancelled'
  from public.appointments as appointment
 where notification.appointment_id = appointment.id
   and notification.clinic_id = appointment.clinic_id
   and notification.notification_type = 'appointment_reminder'
   and notification.status in ('pending', 'processing')
   and appointment.status = 'confirmed'
   and appointment.start_at - pg_catalog.now() <= pg_catalog.make_interval(hours => 24);

-- Ensure every confirmed future appointment has a final reminder.
insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, appointment_id,
    notification_type, channel, reminder_number, scheduled_at,
    event_key, template_data
)
select
    a.clinic_id, a.contact_id, a.booking_request_id, a.id,
    'final_reminder', 'telegram',
    (
        select coalesce(max(n.reminder_number), 0) + 1
          from public.outbound_notifications as n
         where n.clinic_id = a.clinic_id
           and n.appointment_id = a.id
           and n.notification_type = 'final_reminder'
    ),
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
  and not exists (
      select 1 from public.outbound_notifications as n
       where n.clinic_id = a.clinic_id
         and n.appointment_id = a.id
         and n.notification_type = 'final_reminder'
         and n.status in ('pending', 'processing', 'sent')
  )
on conflict do nothing;

-- Ensure appointments more than 24 hours away also have a 24-hour reminder.
insert into public.outbound_notifications (
    clinic_id, contact_id, booking_request_id, appointment_id,
    notification_type, channel, reminder_number, scheduled_at,
    event_key, template_data
)
select
    a.clinic_id, a.contact_id, a.booking_request_id, a.id,
    'appointment_reminder', 'telegram',
    (
        select coalesce(max(n.reminder_number), 0) + 1
          from public.outbound_notifications as n
         where n.clinic_id = a.clinic_id
           and n.appointment_id = a.id
           and n.notification_type = 'appointment_reminder'
    ),
    a.start_at - pg_catalog.make_interval(hours => 24),
    'appointment_reminder:' || a.id::text || ':' || pg_catalog.gen_random_uuid()::text,
    pg_catalog.jsonb_build_object(
        'appointment_reference', a.appointment_reference,
        'start_at', a.start_at,
        'payment_mode', a.payment_mode,
        'payment_status', a.payment_status
    )
from public.appointments as a
where a.status = 'confirmed'
  and a.start_at - pg_catalog.now() > pg_catalog.make_interval(hours => 24)
  and not exists (
      select 1 from public.outbound_notifications as n
       where n.clinic_id = a.clinic_id
         and n.appointment_id = a.id
         and n.notification_type = 'appointment_reminder'
         and n.status in ('pending', 'processing', 'sent')
  )
on conflict do nothing;

commit;
