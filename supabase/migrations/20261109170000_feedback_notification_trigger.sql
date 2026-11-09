begin;

-- Schedule a feedback request two hours after an appointment is completed.
create or replace function private.schedule_feedback_request()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.status = 'completed'
       and (tg_op = 'INSERT' or old.status is distinct from 'completed') then
        insert into public.outbound_notifications (
            clinic_id, contact_id, booking_request_id, appointment_id,
            notification_type, channel, scheduled_at, event_key, template_data
        ) values (
            new.clinic_id, new.contact_id, new.booking_request_id, new.id,
            'feedback_request', 'telegram',
            pg_catalog.now() + pg_catalog.make_interval(hours => 2),
            'feedback_request:' || new.id::text,
            pg_catalog.jsonb_build_object(
                'appointment_reference', new.appointment_reference
            )
        )
        on conflict (clinic_id, event_key) where event_key is not null do nothing;
    end if;
    return new;
end;
$$;

revoke all on function private.schedule_feedback_request()
    from public, anon, authenticated, service_role;

drop trigger if exists appointments_schedule_feedback on public.appointments;
create trigger appointments_schedule_feedback
after update of status on public.appointments
for each row execute function private.schedule_feedback_request();

commit;
