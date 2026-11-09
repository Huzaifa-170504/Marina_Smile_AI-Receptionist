begin;

-- Remove personal identifiers from a cancelled booking while preserving the
-- non-identifying appointment/payment/audit records required by the append-only
-- audit trail and financial history.
--
-- If the patient has another active or upcoming booking, their identity is
-- retained so that booking remains usable. Receipt images are returned to the
-- caller for deletion from Storage.
create or replace function public.anonymize_cancelled_booking(p_booking_request_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row public.booking_requests%rowtype;
    proof_paths text[];
    has_other_active boolean := false;
begin
    if not private.is_service_role() then
        raise exception using message = 'Booking privacy cleanup requires the trusted server role', errcode = '42501';
    end if;

    select * into request_row
      from public.booking_requests
     where id = p_booking_request_id
     for update;
    if not found then
        raise exception using message = 'Booking request not found', errcode = 'P0002';
    end if;

    if request_row.status <> 'cancelled'
       and exists (
           select 1 from public.appointments
            where booking_request_id = p_booking_request_id
              and status in ('pending', 'confirmed')
       ) then
        raise exception using message = 'Booking must be cancelled before cleanup', errcode = 'check_violation';
    end if;

    select exists (
        select 1 from public.booking_requests
         where patient_id = request_row.patient_id
           and id <> p_booking_request_id
           and status in (
               'awaiting_patient_info', 'awaiting_payment', 'payment_submitted',
               'payment_rejected', 'payment_verified', 'awaiting_slot_selection',
               'converted_to_appointment'
           )
    ) or exists (
        select 1 from public.appointments
         where patient_id = request_row.patient_id
           and booking_request_id is distinct from p_booking_request_id
           and status in ('pending', 'confirmed')
    ) into has_other_active;

    select coalesce(array_agg(proof_storage_path), '{}'::text[]) into proof_paths
      from public.payments
     where booking_request_id = p_booking_request_id
       and proof_storage_path is not null;

    update public.outbound_notifications
       set status = 'cancelled'
     where booking_request_id = p_booking_request_id
       and status in ('pending', 'processing');

    if has_other_active then
        return jsonb_build_object(
            'personal_data_removed', false,
            'reason', 'The patient has another active booking.',
            'proof_paths', to_jsonb(proof_paths)
        );
    end if;

    update public.patients
       set full_name = 'Deleted Patient',
           date_of_birth = null,
           age_at_registration = null,
           gender = 'other',
           guardian_name = null,
           guardian_contact = null,
           guardian_relationship = null,
           active = false
     where id = request_row.patient_id;

    update public.contacts
       set name = 'Deleted Contact',
           phone_number = null,
           email = null,
           telegram_chat_id = null,
           preferred_language = null
     where id = request_row.contact_id;

    return jsonb_build_object(
        'personal_data_removed', true,
        'proof_paths', to_jsonb(proof_paths)
    );
end;
$$;

revoke all on function public.anonymize_cancelled_booking(uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.anonymize_cancelled_booking(uuid) to service_role;

comment on function public.anonymize_cancelled_booking(uuid) is
    'Trusted-server-only privacy cleanup. Erases patient/contact identifiers for a cancelled booking unless another active booking requires them.';

commit;
