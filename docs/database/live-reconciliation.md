# Live schema reconciliation handoff

> **ARCHIVED — already applied; do not run.**
>
> This document records a one-off history repair prepared for an earlier
> snapshot. As of 2026-11-18 the live project `lpsjfkyixoubhrryaadk` has
> `0001`–`0016` and every timestamped migration through
> `20261118140000_notification_channel_default.sql` applied. Nothing below is
> pending, and no repair or push command should be executed.
>
> The snapshot also turned out to be incomplete: live
> `appointment_change_requests` was missing `old_service_id` and
> `old_google_calendar_event_id`, so the `0008`/`0014` rows below overstated
> equivalence. `20261118120000_change_request_snapshot_columns.sql` reconciles
> that drift additively. Treat the "verified" column as historical, not as proof
> of the current catalog.

This repository prepares, but does not apply, the reconciliation for Supabase
project `lpsjfkyixoubhrryaadk`. The separately exposed project
`dsbxndbbpryisxevjjgc` is not a target and must not be mutated.

## Order of operations

Only after a backup, a maintenance window, and final approval:

1. Confirm the active CLI project ref is exactly `lpsjfkyixoubhrryaadk`.
2. Re-run the catalog preflight and confirm it still matches the reviewed
   snapshot.
3. Mark only `0001` through `0014` as applied in remote migration history.
4. Dry-run the push and confirm that only `0015` and `0016` are pending.
5. Apply `0015_live_reconciliation.sql`, then
   `0016_patient_operations_foundation.sql`.
6. Run database contract tests, RLS role tests, and security/performance
   advisors before restoring normal traffic.

Historical proposal, retained for provenance only (do not run):

```powershell
npx.cmd --yes supabase@2.120.0 migration repair 0001 0002 0003 0004 0005 0006 0007 0008 0009 0010 0011 0012 0013 0014 --linked --status applied
```

Then inspect, without applying:

```powershell
npx.cmd --yes supabase@2.120.0 db push --linked --dry-run
```

Do not mark `0015` or `0016` applied unless those migrations have actually
committed successfully on the intended project.

## Why 0001-0014 can be history-repaired

The live catalog review established the following equivalence or a known,
explicitly repaired drift. Repairing history records provenance; it does not
execute the old files.

| Version | Verified live foundation | Why marking applied is safe only with 0015 next |
|---|---|---|
| 0001 | Required extensions and helper schema exist, including vector and exclusion-constraint support. | 0015 pins helper search paths and privileges. |
| 0002 | Clinics, staff identities, roles, and permission overrides exist with stable IDs. | 0015 narrows Data API grants without recreating records. |
| 0003 | Dentists, services, mappings, schedules, and tenant relationships exist. | 0015 replaces broad schedule policies. |
| 0004 | Contacts, patients, conversations, and messages exist. | 0015 replaces broad read/write policies with relationship-aware checks. |
| 0005 | Booking requests and payments exist with their current identifiers and relationships. | 0015 restores permission-based payment and booking operations. |
| 0006 | Appointments and the active-overlap exclusion contract exist. | 0015 does not alter or recreate the GiST exclusion constraint. |
| 0007 | Visits, schedule overrides, handoffs, and reminder consents exist. | 0015 tightens schedule and support-queue access. |
| 0008 | Change requests/options, notifications, and audit/tool tables exist. | 0015 restores scoped option access and a trusted notification claimant. |
| 0009 | Knowledge documents/chunks, 768-dimensional embeddings, and HNSW support exist. | 0015 limits Data API knowledge access and service-only matching. |
| 0010 | Auth helper functions exist but have legacy search paths/grants. | 0015 replaces their definitions and revokes excess execution. |
| 0011 | RLS is enabled across application tables but several policies drifted. | 0015 replaces only reviewed policy names and restores owner supervision. |
| 0012 | `clinic-knowledge` and `payment-proofs` exist and are private. | 0015 replaces their policies with clinic-folder isolation; objects are retained. |
| 0013 | The intended seven operational tables are already in Realtime. | 0015 restores `REPLICA IDENTITY FULL` without expanding publication membership. |
| 0014 | Core indexes/triggers and some legacy operational RPC behavior exist, but grants/signatures drifted. | 0015 installs canonical RPC signatures, revokes legacy overloads, and adds only reviewed indexes. |

This mapping is valid only for the reviewed catalog snapshot. If the live
catalog changes, repeat the comparison before repairing history.
