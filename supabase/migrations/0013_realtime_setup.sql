begin;

alter table public.appointments replica identity full;
alter table public.appointment_visits replica identity full;
alter table public.booking_requests replica identity full;
alter table public.payments replica identity full;
alter table public.schedule_overrides replica identity full;
alter table public.appointment_change_requests replica identity full;
alter table public.handoffs replica identity full;

do $$
declare
    v_table_name text;
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        foreach v_table_name in array array[
            'appointments', 'appointment_visits', 'booking_requests', 'payments',
            'schedule_overrides', 'appointment_change_requests', 'handoffs'
        ] loop
            if not exists (
                select 1
                  from pg_publication_tables
                 where pubname = 'supabase_realtime'
                   and schemaname = 'public'
                   and tablename = v_table_name
            ) then
                execute format('alter publication supabase_realtime add table public.%I', v_table_name);
            end if;
        end loop;
    end if;
end
$$;

commit;
