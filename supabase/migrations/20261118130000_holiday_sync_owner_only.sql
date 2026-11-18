begin;

-- Holiday sync internals (sync token, last error, lease) are operational
-- diagnostics, not patient-facing data. Restrict reads to owners and staff who
-- can manage schedules instead of exposing them to every authenticated member.
drop policy if exists clinic_holiday_sync_clinic_select on public.clinic_holiday_sync;
create policy clinic_holiday_sync_clinic_select on public.clinic_holiday_sync
for select to authenticated
using (
    clinic_id = private.current_clinic_id()
    and (
        private.current_staff_role() = 'owner'
        or private.has_permission('schedule.manage_any')
    )
);

commit;
