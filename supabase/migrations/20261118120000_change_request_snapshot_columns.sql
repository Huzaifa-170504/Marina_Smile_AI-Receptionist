begin;

-- Reconciliation: the live public.appointment_change_requests table never
-- received the two nullable snapshot columns that 0008 declares and 0014
-- indexes/validates. Re-add them idempotently so the documented change-request
-- contract matches the live schema. No data is rewritten and nothing is dropped.
alter table public.appointment_change_requests
    add column if not exists old_service_id uuid,
    add column if not exists old_google_calendar_event_id text;

do $$
begin
    if not exists (
        select 1
          from pg_catalog.pg_constraint c
          join pg_catalog.pg_class t on t.oid = c.conrelid
          join pg_catalog.pg_namespace n on n.oid = t.relnamespace
         where n.nspname = 'public'
           and t.relname = 'appointment_change_requests'
           and c.contype = 'f'
           and c.conname = 'appointment_change_requests_old_service_fkey'
    ) then
        alter table public.appointment_change_requests
            add constraint appointment_change_requests_old_service_fkey
            foreign key (clinic_id, old_service_id)
            references public.services(clinic_id, id) on delete restrict;
    end if;
end
$$;

create index if not exists appointment_change_requests_old_service_idx
    on public.appointment_change_requests(old_service_id)
    where old_service_id is not null;

comment on column public.appointment_change_requests.old_service_id is
    'Snapshot of the appointment service before a requested change.';
comment on column public.appointment_change_requests.old_google_calendar_event_id is
    'Snapshot of the Calendar event before a requested change.';

commit;
