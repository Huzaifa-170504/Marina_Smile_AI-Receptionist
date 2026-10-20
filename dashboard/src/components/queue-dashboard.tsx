"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { apiRequest } from "@/lib/api";
import { createClient } from "@/lib/supabase/browser";
import type { QueueStatus } from "@/types/domain";

type QueueRow = {
  id: string;
  queue_status: QueueStatus;
  appointments: {
    id: string;
    start_at: string;
    status: string;
    patients: { patient_number: string; full_name: string; age_at_registration: number | null; gender: string } | null;
    dentists: { name: string } | null;
    services: { name: string } | null;
  } | null;
};

const NEXT_STATE: Partial<Record<QueueStatus, QueueStatus>> = {
  scheduled: "checked_in",
  checked_in: "waiting",
  waiting: "in_progress",
  in_progress: "completed",
};

const ACTION_LABEL: Partial<Record<QueueStatus, string>> = {
  scheduled: "Check in",
  checked_in: "Mark waiting",
  waiting: "Start",
  in_progress: "Complete",
};

export function QueueDashboard() {
  const staff = useStaff();
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const supabase = createClient();
    const { data, error: queryError } = await supabase
      .from("appointment_visits")
      .select("id,queue_status,appointments!inner(id,start_at,status,patients(patient_number,full_name,age_at_registration,gender),dentists(name),services(name))")
      .in("queue_status", ["scheduled", "checked_in", "waiting", "in_progress"])
      .order("created_at", { ascending: true });
    if (queryError) setError("The patient queue could not be loaded with the current access policy.");
    else { setRows((data ?? []) as unknown as QueueRow[]); setError(null); }
    setLoading(false);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useClinicRealtime(staff.clinicId, ["appointments", "appointment_visits"], load);

  async function transition(row: QueueRow, target: QueueStatus) {
    setBusyId(row.id);
    try {
      await apiRequest(`/api/queue/${row.id}/transition`, {
        method: "POST",
        body: JSON.stringify({ expected_status: row.queue_status, target_status: target }),
      });
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Queue update failed"); }
    finally { setBusyId(null); }
  }

  return (
    <>
      <PageHeader eyebrow="Today’s care flow" title={staff.role === "doctor" ? "My patient queue" : "Clinic patient queue"} description="Queue state changes are transactional and reflected only after database confirmation." />
      <section className="panel">
        {loading ? <LoadingState label="Loading today’s patient queue…" /> : null}
        {!loading && error ? <ErrorState message={error} retry={load} /> : null}
        {!loading && !error && rows.length === 0 ? <EmptyState title="No patients are waiting" message="Scheduled visits visible to your role will appear here." /> : null}
        {!loading && !error && rows.length > 0 ? (
          <div className="table-wrap"><table className="data-table">
            <thead><tr><th>Time</th><th>Patient</th><th>Patient no.</th><th>Age / gender</th><th>Doctor</th><th>Service</th><th>Queue</th><th>Action</th></tr></thead>
            <tbody>{rows.map((row) => {
              const appointment = row.appointments;
              const next = NEXT_STATE[row.queue_status];
              return <tr key={row.id}>
                <td data-label="Time">{appointment ? new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(appointment.start_at)) : "—"}</td>
                <td data-label="Patient">{appointment?.patients?.full_name ?? "—"}</td>
                <td data-label="Patient no.">{appointment?.patients?.patient_number ?? "—"}</td>
                <td data-label="Age / gender">{appointment?.patients ? `${appointment.patients.age_at_registration ?? "—"} / ${appointment.patients.gender}` : "—"}</td>
                <td data-label="Doctor">{appointment?.dentists?.name ?? "—"}</td>
                <td data-label="Service">{appointment?.services?.name ?? "—"}</td>
                <td data-label="Queue"><StatusBadge status={row.queue_status} /></td>
                <td data-label="Action"><div className="toolbar">
                  {next ? <button className="primary-button" disabled={busyId === row.id} onClick={() => void transition(row, next)}>{busyId === row.id ? "Updating…" : ACTION_LABEL[row.queue_status]}</button> : null}
                  {["scheduled", "checked_in", "waiting"].includes(row.queue_status) ? <button className="danger-button" disabled={busyId === row.id} onClick={() => void transition(row, "no_show")}>No-show</button> : null}
                </div></td>
              </tr>;
            })}</tbody>
          </table></div>
        ) : null}
      </section>
    </>
  );
}
