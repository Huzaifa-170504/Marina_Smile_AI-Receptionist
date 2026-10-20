# Lifecycle rules

## Booking request

Allowed transitions enforced by `transition_booking_request` are:

- `awaiting_patient_info -> awaiting_payment | cancelled`
- `awaiting_payment -> payment_submitted | expired | cancelled`
- `payment_submitted -> payment_verified | payment_rejected | cancelled`
- `payment_rejected -> payment_submitted | expired | cancelled`
- `payment_verified -> awaiting_slot_selection | cancelled`
- `awaiting_slot_selection -> converted_to_appointment | cancelled`

`converted_to_appointment`, `expired`, and `cancelled` are terminal for this
workflow. Expiring an unpaid request does not delete its patient or any prior
paid history.

## Payment attempt

Normal flow is `awaiting -> submitted -> verified|rejected`. A rejected
submission is retained and a resubmission becomes another payment row. Refund
payout recording moves the linked payment through `verified -> refund_required
-> refund_processing -> refunded` in one database transaction; no gateway
refund is performed by SQL. `verify_payment` locks and conditionally updates a
submitted attempt so concurrent verification cannot run downstream work twice.

## Appointment

The appointment trigger permits:

- `pending -> confirmed`
- `pending -> cancelled`
- `confirmed -> cancelled`
- `confirmed -> completed`
- `confirmed -> no_show`

Same-status updates support rescheduling while preserving `confirmed`.
`cancelled`, `completed`, and `no_show` are terminal. Active `pending` and
`confirmed` ranges are mutually exclusive per dentist. `[start,end)` permits an
appointment ending exactly when another begins.

## Queue / visit

- Trusted FastAPI check-in: `scheduled -> checked_in`, with database validation
  of clinic, appointment, payment/exemption, time window, staff, and retry key.
- Receptionist: `checked_in -> waiting`.
- Assigned Doctor: `waiting -> in_progress -> completed`.

The queue state is not booking status. Confirming an appointment creates its
scheduled visit. Treatment completion through `transition_queue_state` updates
the visit and appointment in one transaction. Owners retain visibility but do
not receive an implicit treatment-transition override.

Check-in tolerance remains application configuration. Only the trusted FastAPI
backend may call `check_in_appointment` and supply bounded early/late values;
the function is not executable by `authenticated` or `anon` roles.

## Refund request

- `requested -> under_review | approved_for_payout | rejected | cancelled`
- `under_review -> approved_for_payout | rejected | cancelled`
- `approved_for_payout -> refund_processing | cancelled`
- `refund_processing -> refunded`

Cancellation and refund state remain separate. `request_refund` derives clinic,
patient, and eligibility from a cancelled appointment and verified payment.
Review RPCs derive the staff actor from Auth. Payout records the payment and
refund transitions atomically, requires proof for online payouts, and returns
the existing completed row when the same payout idempotency key is replayed.

## Doctor deactivation

`pending -> approved|rejected|cancelled`; an approved request becomes `applied`
only through `deactivate_doctor`. Deactivation fails while future pending or
confirmed appointments remain. Dentists are soft-deactivated and historical
appointments retain their foreign keys.

## Appointment change

Change requests use `pending -> accepted|rejected|expired|cancelled`, then
`accepted -> applied` after the deterministic operation succeeds. Proposed
options do not reserve slots. Rescheduling keeps the service, validates dentist
qualification, rechecks availability, and relies on the appointment exclusion
constraint. Calendar compensation remains an application responsibility because
PostgreSQL and Google Calendar cannot share one transaction.
