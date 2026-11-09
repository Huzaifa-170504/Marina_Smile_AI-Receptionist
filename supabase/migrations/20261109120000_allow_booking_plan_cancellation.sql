begin;

-- A booking request that produced an appointment must be cancellable when the
-- patient cancels that appointment (or the whole plan). Without this
-- transition the appointment is cancelled but the request remains reported as
-- converted_to_appointment.
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
        or (old.status = 'converted_to_appointment' and new.status = 'cancelled')
    ) then
        raise exception using
            message = format('Invalid booking request transition: %s -> %s', old.status, new.status),
            errcode = 'check_violation';
    end if;
    return new;
end;
$$;

revoke all on function private.validate_booking_request_transition()
    from public, anon, authenticated, service_role;

commit;
