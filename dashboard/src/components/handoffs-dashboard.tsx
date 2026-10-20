"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { apiRequest } from "@/lib/api";
import { createClient } from "@/lib/supabase/browser";

type Assignee = { id: string; full_name: string };
type Handoff = {
  id: string; reason: string; summary: string | null; priority: string;
  status: "open" | "in_progress" | "resolved"; created_at: string;
  assigned_to_staff_id: string | null;
  contacts: { name: string | null; phone_number: string | null } | null;
  conversations: { id: string; channel: string; status: string } | null;
  staff_users: { full_name: string } | null;
};

export function HandoffsDashboard() {
  const staff = useStaff();
  const [rows, setRows] = useState<Handoff[]>([]);
  const [assignees, setAssignees] = useState<Assignee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const client = createClient();
    const [{ data, error: queryError }, assigneeResult] = await Promise.all([
      client.from("handoffs").select("id,reason,summary,priority,status,created_at,assigned_to_staff_id,contacts(name,phone_number),conversations(id,channel,status),staff_users(full_name)").order("created_at", { ascending: false }),
      apiRequest<Assignee[]>("/api/handoffs/assignees").catch(() => []),
    ]);
    if (queryError) setError("Handoffs could not be loaded."); else { setRows((data ?? []) as unknown as Handoff[]); setError(null); }
    setAssignees(assigneeResult); setLoading(false);
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  useClinicRealtime(staff.clinicId, ["handoffs"], load);

  async function update(row: Handoff, target: Handoff["status"], assignedTo: string | null) {
    setBusyId(row.id);
    try {
      await apiRequest(`/api/handoffs/${row.id}`, { method: "PATCH", body: JSON.stringify({ status: target, assigned_to_staff_id: assignedTo }) });
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Handoff update failed"); }
    finally { setBusyId(null); }
  }

  return <>
    <PageHeader eyebrow="Human support" title="Handoffs" description="A clinic support queue for patient requests that automation cannot or should not continue handling." />
    <section className="panel handoff-explainer"><div><h2>When a request needs a person</h2><p className="muted">A handoff transfers responsibility to authorized clinic staff. Typical reasons include a patient asking for a human, a complaint or payment dispute, an unusual booking problem, a safety-sensitive question, AI uncertainty, or required receptionist intervention.</p></div></section>
    <section className="panel">{loading ? <LoadingState /> : null}{!loading && error ? <ErrorState message={error} retry={load} /> : null}{!loading && !error && rows.length === 0 ? <EmptyState title="No handoffs" message="Cases needing human attention will appear here." /> : null}{!loading && rows.length > 0 ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Created</th><th>Patient/contact</th><th>Conversation</th><th>Reason and summary</th><th>Priority</th><th>Status</th><th>Assigned staff</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.created_at))}</td><td>{row.contacts?.name ?? row.contacts?.phone_number ?? "—"}</td><td>{row.conversations ? <><strong>{row.conversations.channel}</strong><br /><small className="muted">{row.conversations.status}</small></> : "—"}</td><td><strong>{row.reason}</strong>{row.summary ? <><br /><small className="muted">{row.summary}</small></> : null}</td><td><StatusBadge status={row.priority} /></td><td><StatusBadge status={row.status} /></td><td><select aria-label={`Assign ${row.reason}`} value={row.assigned_to_staff_id ?? ""} disabled={busyId === row.id} onChange={(event) => void update(row, row.status, event.target.value || null)}><option value="">Unassigned</option>{assignees.map((assignee) => <option key={assignee.id} value={assignee.id}>{assignee.full_name}</option>)}</select></td><td><div className="toolbar handoff-actions">{row.assigned_to_staff_id !== staff.id ? <button className="secondary-button" disabled={busyId === row.id} onClick={() => void update(row, row.status === "resolved" ? "open" : row.status, staff.id)}>Take ownership</button> : null}{row.status === "open" ? <button className="primary-button" disabled={busyId === row.id} onClick={() => void update(row, "in_progress", row.assigned_to_staff_id ?? staff.id)}>Mark in progress</button> : null}{row.status === "in_progress" ? <button className="primary-button" disabled={busyId === row.id} onClick={() => void update(row, "resolved", row.assigned_to_staff_id ?? staff.id)}>Resolve</button> : null}{row.status === "resolved" ? <button className="secondary-button" disabled={busyId === row.id} onClick={() => void update(row, "open", row.assigned_to_staff_id)}>Reopen</button> : null}</div></td></tr>)}</tbody></table></div> : null}</section>
  </>;
}
