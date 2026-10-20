# Row-level security

## Helper model

Authorization helpers live in the non-exposed `private` schema and use
`SECURITY DEFINER` with an explicit search path. They resolve the active
`staff_users` row from `(select auth.uid())`, then expose only the current staff
ID, clinic, role, dentist, permission decision, or patient/contact visibility.
An inactive staff row resolves to no identity and therefore no access.

Permission overrides take precedence over role defaults. Owners default to all
known permissions. Defaults for other roles are deliberately narrow; adding a
new permission identifier grants nothing until the helper is migrated or an
explicit override is stored.

## Role matrix

| Area | Owner | Doctor | Receptionist | Payment verifier |
|---|---|---|---|---|
| Clinic configuration | Read/write own clinic | Read own clinic | Read own clinic | Read own clinic |
| Staff/permissions | Manage | Self only | Self only | Self only |
| Dentists/services | Manage | Read | Read | Read |
| Weekly schedule/overrides | Manage all | Own only | Manage all | None |
| Patients/contacts | All clinic | Assigned appointments only | All clinic | Payment-related limited identity |
| Appointments | All clinic | Assigned read | Create/read/reschedule/cancel | None |
| Queue | All clinic visibility | Own treatment transitions through controlled RPC | Check-in and waiting transitions | None |
| Booking requests | All clinic | None | Manage | Payment-related read |
| Payments | All clinic | None | Read and verify through RPC | Read and verify through RPC |
| Payment proofs | Read/write | None | Read/write | Read/write |
| Refunds | Full clinic supervision | None | Review, approve/reject, and record payout | None by default |
| Refund proofs | Read | None | Read/upload | None by default |
| Knowledge files | Read/write | None | None | None |
| Audit | Clinic-wide | Assigned appointment | Clinic-wide | No general access |

The backend uses only server-side service-role credentials for WhatsApp,
Calendar synchronization, reminders, expiry, payment media ingestion, and RAG
ingestion. No browser receives service-role credentials.

## Storage

`payment-proofs`, `refund-proofs`, and `clinic-knowledge` are private. The first path segment
must equal the authenticated staff member's clinic UUID.

- Payment proof read: Owner or `payment.view_proof`/`payment.verify`.
- Payment proof write: Owner or `payment.verify`.
- Refund proof read: Owner or `refund.view_proof`.
- Refund proof upload/update: staff resolving both `refund.manage` and
  `refund.view_proof`.
- Clinic knowledge: Owner only through browser policies; trusted server jobs may
  use service role.
- Doctors receive no payment/refund proof access. Payment Verifiers receive no
  refund access unless a future explicit permission override grants it.

## Test plan

Run `supabase/tests/002_rls_preflight.sql` after migrations. Then create real Auth
users for Owner, Doctor, Receptionist, Payment Verifier, and an inactive staff
fixture. With each user's JWT, verify:

1. Owner can see only its clinic and can manage clinic-wide operational rows.
2. Doctor sees own appointments/queue and assigned patient identity, not another
   doctor's queue or any payment proof.
3. Receptionist manages appointments but cannot modify staff permissions.
4. Payment Verifier sees relevant booking/payment data and proofs but cannot
   manage schedules.
5. Inactive, cross-clinic, and unauthenticated requests return no private rows.

RLS is defense in depth. RPCs and the backend must still validate business
transitions and never treat a successful SELECT as authorization to mutate.
