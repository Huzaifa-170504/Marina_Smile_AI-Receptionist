begin;

insert into storage.buckets (id, name, public, file_size_limit)
values
    ('payment-proofs', 'payment-proofs', false, 10485760),
    ('clinic-knowledge', 'clinic-knowledge', false, 52428800)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit;

create policy payment_proofs_authorized_read on storage.objects
for select to authenticated
using (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.view_proof')
        or private.has_permission('payment.verify')
    )
);

create policy payment_proofs_authorized_insert on storage.objects
for insert to authenticated
with check (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
);

create policy payment_proofs_authorized_update on storage.objects
for update to authenticated
using (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
)
with check (
    bucket_id = 'payment-proofs'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('payment.verify')
    )
);

create policy clinic_knowledge_owner_read on storage.objects
for select to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_insert on storage.objects
for insert to authenticated
with check (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_update on storage.objects
for update to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
)
with check (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

create policy clinic_knowledge_owner_delete on storage.objects
for delete to authenticated
using (
    bucket_id = 'clinic-knowledge'
    and (storage.foldername(name))[1] = private.current_clinic_id()::text
    and private.current_staff_role() = 'owner'
);

commit;
