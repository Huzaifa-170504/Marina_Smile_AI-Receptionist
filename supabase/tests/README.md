# Database tests

Apply all migrations to an empty disposable Supabase project, apply the demo
seed, then run the SQL files in numeric order. Each test file wraps synthetic
fixtures in a transaction and rolls them back.

`001_integrity.sql` checks overlap rejection, adjacent intervals, tenant foreign
keys, message/reminder/idempotency uniqueness, queue transitions, doctor-future
appointment lookup, the 768-dimensional vector contract, tenant-filtered RAG,
and retention after booking expiry.

`002_rls_preflight.sql` verifies structural RLS and private Storage setup. The
JWT/role matrix requires actual Supabase Auth users and is intentionally a
controlled post-provisioning test; follow `docs/database/rls.md`.

`003_rls_role_matrix.sql` executes the Auth role matrix. The focused
`004_patient_operations_foundation.sql` test validates durable workflow state,
references and idempotency, refunds and refund RLS, secure check-in, and
role-separated clinical queue transitions. `005_live_reconciliation_contract.sql`
checks the final catalog for least-privilege RPC/table grants, scoped policies,
private Storage, replica identity, and preserved appointment-overlap support.

Do not run these files against a production project without reviewing the IDs
and transaction behavior first.
