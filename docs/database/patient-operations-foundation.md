# Patient operations database foundation

Migration `0016_patient_operations_foundation.sql` is a local-only foundation.
It must not be applied remotely without explicit approval. The repository CLI
link is verified as `lpsjfkyixoubhrryaadk`; the separately exposed project
`dsbxndbbpryisxevjjgc` is unrelated and must not be mutated.

## Check-in configuration boundary

Check-in tolerance remains FastAPI-owned configuration. The browser cannot call
`check_in_appointment`; only `service_role` has execute permission. FastAPI will
authenticate the employee, resolve the staff row, and pass that server-resolved
staff ID plus bounded early/late configuration values. The RPC independently
loads the active Receptionist, clinic, appointment, service, booking request,
payment, and visit before changing state.

This avoids hardcoding clinic policy in SQL while preventing a browser from
selecting its own tolerance, clinic, payment state, appointment state, or staff
identity.

## Application compatibility work intentionally deferred

The migration changes the database contract. The following application work is
required in the next approved phase:

- `app/api/auth.py`: add refund permissions and Receptionist payment/refund
  defaults so FastAPI matches PostgreSQL.
- `dashboard/src/types/domain.ts` and `dashboard/src/lib/permissions.ts`: add
  human-readable refund permissions and matching role defaults.
- `app/core/config.py` and `.env.example`: add bounded check-in early/late
  configuration.
- `app/api/rpc.py`: add a trusted-server RPC caller for the service-only
  check-in operation.
- `app/api/routes/queue.py` and `app/api/models.py`: adopt the three-argument
  queue RPC and add the check-in request contract.
- `app/api/routes/payments.py`: integrate payment submission idempotency and
  logical notification event keys.
- `app/operations/booking.py`, `app/repositories/appointments.py`, and
  `app/operations/appointment_lookup.py`: propagate booking idempotency and
  patient-facing appointment references without treating references as secrets.
- `app/operations/reschedule.py` and `app/operations/cancellation.py`: enqueue
  idempotent template notifications.
- Add focused conversation-workflow and refund repositories/services/routes;
  register them in `app/main.py`.
- `dashboard/src/components/payments-dashboard.tsx`: expose Receptionist payment
  review using the existing protected backend operations.
- `dashboard/src/components/queue-dashboard.tsx`: separate Receptionist
  check-in/waiting actions from Doctor treatment actions.
- Add Owner/Receptionist refund pages and components, plus navigation entries in
  `dashboard/src/lib/navigation.ts` and `dashboard/src/components/dashboard-shell.tsx`.

No patient interaction, refund dashboard, check-in endpoint, or notification
transport is implemented by migration 0016 itself.

## Local validation

Run migrations `0001` through `0016`, apply
`supabase/seeds/seed_demo_marina_smile.sql`, then execute SQL tests `001` through
`004` in numeric order against a disposable local Supabase database. Test `004`
rolls back all fixtures.

Static parsing is useful but does not replace executing the chain. Docker or an
equivalent disposable PostgreSQL/Supabase environment is required for the full
validation.
