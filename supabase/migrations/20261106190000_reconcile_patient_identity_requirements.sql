begin;

-- Reconcile the live schema with the canonical patient model.
--
-- The live database requires patients.date_of_birth or age_at_registration and
-- requires contacts.phone_number. Telegram registration intentionally supports
-- patients who provide only a name plus a phone/email, and email-only contacts,
-- so align the constraints with 0004_contacts_patients_messaging.sql.

alter table public.contacts
    alter column phone_number drop not null;

alter table public.patients
    drop constraint if exists patients_check;

alter table public.patients
    add constraint patients_check
    check (date_of_birth is null or date_of_birth <= current_date);

comment on constraint patients_check on public.patients is
    'Date of birth must not be in the future. Date of birth and age are optional for Telegram registration.';

commit;
