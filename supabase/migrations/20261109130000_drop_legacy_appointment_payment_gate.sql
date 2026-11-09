begin;

-- The live database has a legacy trigger that requires a verified payment for
-- every appointment, including cash bookings, and therefore blocks the
-- supported cash-in-hand flow.
--
-- The canonical private.validate_appointment_write() trigger enforces the
-- intended rule:
--   * cash bookings may be confirmed with payment_status = 'unpaid'
--   * online bookings require a matching verified payment
--   * owner-approved payment exemptions remain valid
--
-- Remove only the legacy trigger. Keep its function in place so the change is
-- non-destructive and reversible.
drop trigger if exists trg_enforce_appointment_payment_gate on public.appointments;

-- Ensure the canonical payment/eligibility gate is present.
do $$
begin
    if not exists (
        select 1
          from pg_catalog.pg_trigger as trigger_row
         where trigger_row.tgrelid = 'public.appointments'::regclass
           and trigger_row.tgname = 'appointments_validate_write'
           and not trigger_row.tgisinternal
    ) then
        create trigger appointments_validate_write
        before insert or update on public.appointments
        for each row execute function private.validate_appointment_write();
    end if;
end;
$$;

comment on function public.enforce_appointment_payment_gate() is
    'Legacy payment gate. Its trigger was removed because it blocked cash bookings; the canonical private.validate_appointment_write() gate is authoritative.';

commit;
