begin;

create schema if not exists extensions;
create schema if not exists private;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists vector with schema extensions;
create extension if not exists btree_gist with schema extensions;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = pg_catalog.now();
    return new;
end;
$$;

revoke all on function public.set_updated_at() from public, anon, authenticated;

comment on function public.set_updated_at() is
    'Shared trigger for mutable public tables with an updated_at column.';

commit;
