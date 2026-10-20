"use client";

import { useCallback, useEffect, useState } from "react";

import { ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { apiRequest } from "@/lib/api";
import { roleLabel } from "@/lib/permissions";
import type { OverviewMetrics } from "@/types/domain";

type ApiOverview = {
  todays_appointments: number;
  waiting_patients: number;
  in_progress_patients: number;
  completed_today: number;
  upcoming_appointments: number;
  pending_payments: number;
  todays_cancellations: number;
  no_shows_today: number;
};

export function OverviewDashboard() {
  const staff = useStaff();
  const [metrics, setMetrics] = useState<OverviewMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const data = await apiRequest<ApiOverview>("/api/dashboard/overview");
      setMetrics({
        todaysAppointments: data.todays_appointments,
        waitingPatients: data.waiting_patients,
        inProgressPatients: data.in_progress_patients,
        completedToday: data.completed_today,
        upcomingAppointments: data.upcoming_appointments,
        pendingPayments: data.pending_payments,
        todaysCancellations: data.todays_cancellations,
        noShowsToday: data.no_shows_today,
      });
      setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unexpected error"); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useClinicRealtime(staff.clinicId, ["appointments", "appointment_visits", "payments"], load);

  const cards = metrics ? [
    ["Today's appointments", metrics.todaysAppointments],
    ["Waiting patients", metrics.waitingPatients],
    ["In progress", metrics.inProgressPatients],
    ["Completed today", metrics.completedToday],
    ["Upcoming", metrics.upcomingAppointments],
    ["Payments to verify", metrics.pendingPayments],
    ["Cancellations today", metrics.todaysCancellations],
    ["No-shows today", metrics.noShowsToday],
  ] as const : [];

  return (
    <>
      <PageHeader eyebrow={`${roleLabel(staff.role)} workspace`} title={`Good day, ${staff.fullName.split(" ")[0]}`} description="Today’s operational picture, refreshed when clinic activity changes." />
      {error ? <div className="panel"><ErrorState message={error} retry={load} /></div> : null}
      {!metrics && !error ? <div className="panel"><LoadingState /></div> : null}
      {metrics ? <div className="metrics-grid">{cards.map(([label, value]) => <article className="metric-card" key={label}><span className="metric-label">{label}</span><strong>{value}</strong></article>)}</div> : null}
      <section className="panel">
        <div className="panel-header"><h2>Operational safeguards</h2></div>
        <div className="notice">Availability, payments, queue transitions, and permissions remain database-authoritative. Dashboard actions are shown only after protected operations confirm success.</div>
      </section>
    </>
  );
}
