begin;

alter table public.contacts
    add column email text;

create index contacts_clinic_email_lookup_idx
    on public.contacts (clinic_id, lower(btrim(email)))
    where email is not null and btrim(email) <> '';

comment on column public.contacts.email is
    'Optional patient-contact email used for exact, clinic-scoped contact verification.';

commit;
