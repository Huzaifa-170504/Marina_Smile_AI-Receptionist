begin;

alter table public.contacts
    add column if not exists preferred_notification_channel text not null default 'telegram',
    add constraint contacts_preferred_notification_channel_check
        check (preferred_notification_channel in ('telegram', 'whatsapp'));

comment on column public.contacts.preferred_notification_channel is
    'Patient channel selected by starting an appointment booking in a verified messaging conversation.';

-- Preserve an existing WhatsApp conversation as the patient's preference and
-- move only notifications that have not yet been delivered.
update public.contacts as contact
   set preferred_notification_channel = 'whatsapp'
 where exists (
       select 1
         from public.conversations as conversation
        where conversation.clinic_id = contact.clinic_id
          and conversation.contact_id = contact.id
          and conversation.channel = 'whatsapp'
   );

update public.outbound_notifications as notification
   set channel = contact.preferred_notification_channel
  from public.contacts as contact
 where notification.clinic_id = contact.clinic_id
   and notification.contact_id = contact.id
   and notification.status = 'pending'
   and contact.preferred_notification_channel = 'whatsapp';

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
    next_scheduled_at timestamptz;
    template jsonb;
    within_twenty_four_hours boolean;
    notification_channel text := 'telegram';
begin
    if new.status = 'confirmed'
       and (
           tg_op = 'INSERT'
           or old.status is distinct from 'confirmed'
           or old.start_at is distinct from new.start_at
       ) then
        select coalesce(contact.preferred_notification_channel, 'telegram')
          into notification_channel
          from public.contacts as contact
         where contact.clinic_id = new.clinic_id
           and contact.id = new.contact_id;

        within_twenty_four_hours :=
            (new.start_at - pg_catalog.now()) <= pg_catalog.make_interval(hours => 24);

        template := pg_catalog.jsonb_build_object(
            'appointment_reference', new.appointment_reference,
            'start_at', new.start_at,
            'payment_mode', new.payment_mode,
            'payment_status', new.payment_status
        );

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
                  ('appointment_reminder', pg_catalog.make_interval(hours => 24), not within_twenty_four_hours),
                  ('final_reminder', pg_catalog.make_interval(hours => 2), true)
              ) as entries(notification_type, lead_time, should_schedule)
        loop
            if not reminder.should_schedule then
                continue;
            end if;

            next_scheduled_at := new.start_at - reminder.lead_time;
            if next_scheduled_at <= pg_catalog.now() then
                next_scheduled_at := pg_catalog.now() + pg_catalog.make_interval(mins => 1);
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
                   set scheduled_at = next_scheduled_at,
                       template_data = template,
                       channel = notification_channel
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
                    reminder.notification_type, notification_channel, next_number, next_scheduled_at,
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

create or replace function private.schedule_feedback_request()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    notification_channel text := 'telegram';
begin
    if new.status = 'completed'
       and (tg_op = 'INSERT' or old.status is distinct from 'completed') then
        select coalesce(contact.preferred_notification_channel, 'telegram')
          into notification_channel
          from public.contacts as contact
         where contact.clinic_id = new.clinic_id
           and contact.id = new.contact_id;

        insert into public.outbound_notifications (
            clinic_id, contact_id, booking_request_id, appointment_id,
            notification_type, channel, scheduled_at, event_key, template_data
        ) values (
            new.clinic_id, new.contact_id, new.booking_request_id, new.id,
            'feedback_request', notification_channel,
            pg_catalog.now() + pg_catalog.make_interval(hours => 2),
            'feedback_request:' || new.id::text,
            pg_catalog.jsonb_build_object('appointment_reference', new.appointment_reference)
        )
        on conflict (clinic_id, event_key) where event_key is not null do nothing;
    end if;
    return new;
end;
$$;

revoke all on function private.schedule_feedback_request()
    from public, anon, authenticated, service_role;

drop function public.claim_outbound_notification();

create function public.claim_outbound_notification(
    p_channels text[] default array['telegram', 'whatsapp']::text[]
)
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
    if p_channels is null or cardinality(p_channels) = 0
       or not (p_channels <@ array['telegram', 'whatsapp']::text[]) then
        raise exception using message = 'Notification channels are invalid', errcode = '22023';
    end if;

    select * into result
      from public.outbound_notifications
     where status = 'pending'
       and channel = any(p_channels)
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

revoke all on function public.claim_outbound_notification(text[])
    from public, anon, authenticated, service_role;
grant execute on function public.claim_outbound_notification(text[])
    to service_role;

commit;
