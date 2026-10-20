begin;

create table public.knowledge_documents (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    filename text not null check (btrim(filename) <> ''),
    storage_path text,
    mime_type text,
    checksum text not null check (checksum ~ '^[0-9a-fA-F]{64}$'),
    version integer not null default 1 check (version > 0),
    status text not null default 'pending' check (status in ('pending', 'processing', 'ready', 'failed')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (clinic_id, id),
    unique (clinic_id, checksum)
);

create table public.knowledge_chunks (
    id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinics(id) on delete restrict,
    document_id uuid not null,
    content text not null check (btrim(content) <> ''),
    embedding extensions.vector(768) not null,
    page_number integer check (page_number is null or page_number > 0),
    section text,
    chunk_index integer not null check (chunk_index >= 0),
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz not null default now(),
    foreign key (clinic_id, document_id)
        references public.knowledge_documents(clinic_id, id) on delete cascade,
    unique (document_id, chunk_index)
);

create index knowledge_chunks_embedding_hnsw
    on public.knowledge_chunks
    using hnsw (embedding extensions.vector_cosine_ops);

create or replace function public.match_knowledge_chunks(
    p_clinic_id uuid,
    p_query_embedding extensions.vector(768),
    p_match_threshold double precision,
    p_match_count integer
)
returns table (
    chunk_id uuid,
    document_id uuid,
    filename text,
    content text,
    page_number integer,
    section text,
    metadata jsonb,
    similarity double precision
)
language sql
stable
security invoker
set search_path = pg_catalog, public, extensions
as $$
    select
        kc.id as chunk_id,
        kc.document_id,
        kd.filename,
        kc.content,
        kc.page_number,
        kc.section,
        kc.metadata,
        (1 - (kc.embedding <=> p_query_embedding))::double precision as similarity
    from public.knowledge_chunks as kc
    join public.knowledge_documents as kd
      on kd.id = kc.document_id
     and kd.clinic_id = kc.clinic_id
    where kc.clinic_id = p_clinic_id
      and kd.status = 'ready'
      and (1 - (kc.embedding <=> p_query_embedding)) >= greatest(0.0, least(1.0, p_match_threshold))
    order by kc.embedding <=> p_query_embedding
    limit greatest(1, least(coalesce(p_match_count, 5), 50));
$$;

create trigger knowledge_documents_set_updated_at
before update on public.knowledge_documents
for each row execute function public.set_updated_at();

comment on function public.match_knowledge_chunks(uuid, extensions.vector, double precision, integer) is
    'Tenant-filtered cosine search over ready clinic knowledge only; result count is capped at 50.';

commit;
