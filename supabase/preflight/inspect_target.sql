-- Read-only inventory for the exact Supabase project selected for initialization.
-- Run before applying any migration; this file performs no mutation.

select current_database() as database_name, current_user as database_user;

select n.nspname as schema_name, c.relname as object_name,
       c.relkind as object_kind, c.relrowsecurity as rls_enabled
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public'
   and c.relkind in ('r', 'p', 'v', 'm')
 order by c.relkind, c.relname;

select extname, extversion, n.nspname as installed_schema
  from pg_extension e
  join pg_namespace n on n.oid = e.extnamespace
 where extname in ('pgcrypto', 'vector', 'btree_gist')
 order by extname;

select schemaname, tablename, policyname, roles, cmd
  from pg_policies
 where schemaname in ('public', 'storage')
 order by schemaname, tablename, policyname;

select id, name, public, file_size_limit
  from storage.buckets
 where id in ('payment-proofs', 'clinic-knowledge')
 order by id;

select schemaname, tablename
  from pg_publication_tables
 where pubname = 'supabase_realtime'
 order by schemaname, tablename;
