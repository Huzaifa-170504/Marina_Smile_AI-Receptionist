"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { apiRequest } from "@/lib/api";
import { createClient } from "@/lib/supabase/browser";

type Doctor = { id: string; name: string };
type ScheduleRow = { id: string; weekday: number; start_time: string; end_time: string; active: boolean; dentists: { name: string } | null };
type OverrideRow = { id: string; start_at: string; end_at: string; override_type: string; reason: string | null; dentists: { name: string } | null };
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function ScheduleManager() {
  const staff = useStaff();
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [overrides, setOverrides] = useState<OverrideRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const supabase = createClient();
    const [doctorResult, scheduleResult, overrideResult] = await Promise.all([
      supabase.from("dentists").select("id,name").eq("active", true).order("name"),
      supabase.from("dentist_schedules").select("id,weekday,start_time,end_time,active,dentists(name)").order("weekday"),
      supabase.from("schedule_overrides").select("id,start_at,end_at,override_type,reason,dentists(name)").gte("end_at", new Date().toISOString()).order("start_at"),
    ]);
    if (scheduleResult.error || overrideResult.error) setError("Schedule data could not be loaded.");
    else { setSchedules((scheduleResult.data ?? []) as unknown as ScheduleRow[]); setOverrides((overrideResult.data ?? []) as unknown as OverrideRow[]); setDoctors((doctorResult.data ?? []) as Doctor[]); setError(null); }
    setLoading(false);
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  useClinicRealtime(staff.clinicId, ["schedule_overrides"], load);

  async function createOverride(formData: FormData) {
    const dentistId = staff.role === "doctor" ? staff.doctorId : String(formData.get("dentist_id") ?? "");
    if (!dentistId) { setError("Select a doctor."); return; }
    setPending(true); setError(null);
    try {
      await apiRequest("/api/schedule-overrides", { method: "POST", body: JSON.stringify({ dentist_id: dentistId, start_local: formData.get("start_local"), end_local: formData.get("end_local"), override_type: formData.get("override_type"), reason: formData.get("reason") || null }) });
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Schedule override failed"); }
    finally { setPending(false); }
  }

  return <>
    <PageHeader eyebrow="Availability control" title={staff.role === "doctor" ? "My availability" : "Schedules and overrides"} description="Weekly hours are separate from leave, blocked time, and extra availability." />
    <section className="panel"><div className="panel-header"><h2>Add schedule override</h2></div><form className="form-grid" action={(formData) => void createOverride(formData)}>
      {staff.role !== "doctor" ? <div className="field"><label htmlFor="override-doctor">Doctor</label><select id="override-doctor" name="dentist_id" required defaultValue=""><option value="">Select doctor</option>{doctors.map((doctor) => <option key={doctor.id} value={doctor.id}>{doctor.name}</option>)}</select></div> : null}
      <div className="two-column"><div className="field"><label htmlFor="override-start">Start in clinic time</label><input id="override-start" name="start_local" type="datetime-local" required /></div><div className="field"><label htmlFor="override-end">End in clinic time</label><input id="override-end" name="end_local" type="datetime-local" required /></div></div>
      <div className="two-column"><div className="field"><label htmlFor="override-type">Type</label><select id="override-type" name="override_type"><option value="unavailable">Unavailable / leave / block</option><option value="extra_availability">Extra availability</option></select></div><div className="field"><label htmlFor="override-reason">Reason</label><input id="override-reason" name="reason" placeholder="Leave, lunch, extra clinic hours…" /></div></div>
      <button className="primary-button" disabled={pending}>{pending ? "Saving…" : "Add override"}</button>
    </form></section>
    {error ? <section className="panel"><ErrorState message={error} retry={load} /></section> : null}
    <div className="two-column">
      <section className="panel"><div className="panel-header"><h2>Weekly hours</h2></div>{loading ? <LoadingState /> : schedules.length === 0 ? <EmptyState title="No weekly hours" message="No base schedule is visible." /> : <div className="table-wrap"><table className="data-table"><thead><tr><th>Doctor</th><th>Day</th><th>Hours</th><th>State</th></tr></thead><tbody>{schedules.map((row) => <tr key={row.id}><td data-label="Doctor">{row.dentists?.name ?? "—"}</td><td data-label="Day">{WEEKDAYS[row.weekday] ?? row.weekday}</td><td data-label="Hours">{row.start_time.slice(0,5)}–{row.end_time.slice(0,5)}</td><td data-label="State"><StatusBadge status={row.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div>}</section>
      <section className="panel"><div className="panel-header"><h2>Upcoming overrides</h2></div>{loading ? <LoadingState /> : overrides.length === 0 ? <EmptyState title="No upcoming overrides" message="Weekly hours remain in effect." /> : <div className="stack">{overrides.map((row) => <article key={row.id} className="metric-card"><strong style={{ fontSize: "1rem" }}>{row.dentists?.name ?? "Doctor"}</strong><p className="muted">{new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.start_at))}</p><StatusBadge status={row.override_type} />{row.reason ? <p>{row.reason}</p> : null}</article>)}</div>}</section>
    </div>
  </>;
}
