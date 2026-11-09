begin;

-- One Telegram chat may manage more than one patient (for example, family
-- members registered under different phone numbers or emails). The chat link
-- is still stored and checked for privacy; it is no longer unique per clinic.
drop index if exists public.contacts_clinic_telegram_unique;

create index if not exists contacts_clinic_telegram_idx
    on public.contacts (clinic_id, telegram_chat_id)
    where telegram_chat_id is not null;

commit;
