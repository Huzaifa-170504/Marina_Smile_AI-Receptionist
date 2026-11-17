begin;

-- Prevent a patient from holding two active appointments for the same service
-- and dentist. Different services or different doctors remain allowed, and
-- cancelled appointments release the service. The advisory lock serializes
-- concurrent reservations for the same patient/dentist/service so the check
-- and insert are atomic.
create or replace function public.reserve_appointment(
    p_clinic_id uuid,
    p_booking_request_id uuid,
    p_payment_id uuid,
    p_dentist_id uuid,
    p_start_at timestamptz,
    p_end_at timestamptz,
    p_idempotency_key uuid
)
returns public.appointments
language plpgsql
security definer
set search_path = ''
as $$
declare
    request_row public.booking_requests%rowtype;
    result public.appointments%rowtype;
    line_count integer;
    unqualified_count integer;
    line record;
begin
    if not private.is_service_role() then
        raise exception using message = 'Appointment reservation requires the trusted server role', errcode = '42501';
    end if;
    if p_clinic_id is null or p_booking_request_id is null or p_dentist_id is null
       or p_start_at is null or p_end_at is null or p_idempotency_key is null then
        raise exception using message = 'Reservation arguments must not be null', errcode = '22023';
    end if;
    if p_end_at <= p_start_at then
        raise exception using message = 'Appointment end must be later than start', errcode = '22023';
    end if;

    select * into result
      from public.appointments
     where idempotency_key = p_idempotency_key;
    if found then
        if result.clinic_id <> p_clinic_id
           or result.booking_request_id <> p_booking_request_id
           or result.payment_id is distinct from p_payment_id
           or result.dentist_id <> p_dentist_id
           or result.start_at <> p_start_at
           or result.end_at <> p_end_at then
            raise exception using message = 'Idempotency key belongs to another reservation', errcode = '23505';
        end if;
        return result;
    end if;

    select * into request_row
      from public.booking_requests
     where id = p_booking_request_id
       and clinic_id = p_clinic_id
     for update;
    if not found then
        raise exception using message = 'Booking request not found', errcode = 'P0002';
    end if;
    if request_row.status not in ('payment_verified', 'awaiting_slot_selection')
       and not request_row.payment_exempt then
        raise exception using message = 'Booking request is not ready for reservation', errcode = 'check_violation';
    end if;

    select count(*) into line_count
      from public.booking_request_services
     where clinic_id = p_clinic_id
       and booking_request_id = p_booking_request_id;

    if line_count = 0 then
        if not exists (
            select 1
              from public.dentists as d
              join public.dentist_services as ds
                on ds.clinic_id = d.clinic_id
               and ds.dentist_id = d.id
               and ds.service_id = request_row.service_id
             where d.id = p_dentist_id
               and d.clinic_id = p_clinic_id
               and d.active
        ) then
            raise exception using message = 'Dentist is inactive, outside the clinic, or unqualified', errcode = 'check_violation';
        end if;

        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                request_row.patient_id::text || ':' || p_dentist_id::text || ':' ||
                    request_row.service_id::text,
                0
            )
        );
        if exists (
            select 1
              from public.appointment_services as existing_line
              join public.appointments as existing_appointment
                on existing_appointment.id = existing_line.appointment_id
             where existing_appointment.clinic_id = p_clinic_id
               and existing_appointment.patient_id = request_row.patient_id
               and existing_appointment.dentist_id = p_dentist_id
               and existing_appointment.status in ('pending', 'confirmed')
               and existing_appointment.end_at >= pg_catalog.now()
               and existing_line.service_id = request_row.service_id
        ) then
            raise exception using
                message = 'You have already booked this service with this doctor. Please cancel your existing booking before making a new one.',
                errcode = '23505';
        end if;
    else
        select count(*) into unqualified_count
          from public.booking_request_services as lines
         where lines.clinic_id = p_clinic_id
           and lines.booking_request_id = p_booking_request_id
           and not exists (
               select 1
                 from public.dentist_services as ds
                 join public.dentists as d
                   on d.clinic_id = ds.clinic_id
                  and d.id = ds.dentist_id
                where ds.clinic_id = p_clinic_id
                  and ds.dentist_id = p_dentist_id
                  and ds.service_id = lines.service_id
                  and d.active
           );
        if unqualified_count > 0 then
            raise exception using message = 'Dentist does not provide every selected service', errcode = 'check_violation';
        end if;

        for line in
            select service_id
              from public.booking_request_services
             where clinic_id = p_clinic_id
               and booking_request_id = p_booking_request_id
        loop
            perform pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    request_row.patient_id::text || ':' || p_dentist_id::text || ':' ||
                        line.service_id::text,
                    0
                )
            );
            if exists (
                select 1
                  from public.appointment_services as existing_line
                  join public.appointments as existing_appointment
                    on existing_appointment.id = existing_line.appointment_id
                 where existing_appointment.clinic_id = p_clinic_id
                   and existing_appointment.patient_id = request_row.patient_id
                   and existing_appointment.dentist_id = p_dentist_id
                   and existing_appointment.status in ('pending', 'confirmed')
               and existing_appointment.end_at >= pg_catalog.now()
                   and existing_line.service_id = line.service_id
            ) then
                raise exception using
                    message = 'You have already booked this service with this doctor. Please cancel your existing booking before making a new one.',
                    errcode = '23505';
            end if;
        end loop;
    end if;

    insert into public.appointments (
        clinic_id, patient_id, contact_id, booking_request_id, payment_id,
        service_id, dentist_id, start_at, end_at, status, idempotency_key,
        payment_mode, payment_status, total_amount, currency
    ) values (
        request_row.clinic_id, request_row.patient_id, request_row.contact_id,
        request_row.id, p_payment_id, request_row.service_id, p_dentist_id,
        p_start_at, p_end_at, 'pending', p_idempotency_key,
        coalesce(request_row.payment_mode, 'online'),
        request_row.payment_status,
        request_row.total_amount,
        coalesce(request_row.currency, 'AED')
    ) returning * into result;

    insert into public.appointment_services (
        clinic_id, appointment_id, service_id, service_name,
        price_amount, duration_minutes, currency
    )
    select lines.clinic_id, result.id, lines.service_id, lines.service_name,
           lines.price_amount, lines.duration_minutes, lines.currency
      from public.booking_request_services as lines
     where lines.clinic_id = p_clinic_id
       and lines.booking_request_id = p_booking_request_id
    on conflict (clinic_id, appointment_id, service_id) do nothing;

    if line_count = 0 then
        insert into public.appointment_services (
            clinic_id, appointment_id, service_id, service_name,
            price_amount, duration_minutes, currency
        )
        select result.clinic_id, result.id, service.id, service.name,
               coalesce(service.price_from, 0), service.duration_minutes, service.currency
          from public.services as service
         where service.clinic_id = result.clinic_id
           and service.id = result.service_id
        on conflict (clinic_id, appointment_id, service_id) do nothing;
    end if;

    return result;
end;
$$;

revoke all on function public.reserve_appointment(uuid, uuid, uuid, uuid, timestamptz, timestamptz, uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.reserve_appointment(uuid, uuid, uuid, uuid, timestamptz, timestamptz, uuid)
    to service_role;

create index if not exists appointments_active_patient_dentist_idx
    on public.appointments (patient_id, dentist_id, status)
    where status in ('pending', 'confirmed');

create index if not exists appointment_services_service_appointment_idx
    on public.appointment_services (service_id, appointment_id);

commit;
