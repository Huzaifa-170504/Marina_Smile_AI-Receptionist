begin;

-- Deliveries run through Telegram. The legacy default ('whatsapp') could label
-- a row that omitted channel incorrectly while the Telegram worker still sent
-- it. Existing explicit channel values are untouched.
alter table public.outbound_notifications
    alter column channel set default 'telegram';

commit;
