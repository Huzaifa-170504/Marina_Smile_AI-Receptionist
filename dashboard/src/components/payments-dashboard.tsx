"use client";

import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { apiRequest } from "@/lib/api";
import { createClient } from "@/lib/supabase/browser";

type PaymentRow = {
  id: string;
  amount_expected: number | null;
  currency: string;
  proof_storage_path: string | null;
  status: string;
  submitted_at: string | null;
  booking_requests: {
    id: string;
    patients: { patient_number: string; full_name: string } | null;
    services: { name: string } | null;
  } | null;
};

export function PaymentsDashboard() {
  const staff = useStaff();
  const [rows, setRows] = useState<PaymentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const { data, error: queryError } = await createClient()
      .from("payments")
      .select("id,amount_expected,currency,proof_storage_path,status,submitted_at,booking_requests!inner(id,patients(patient_number,full_name),services(name))")
      .eq("status", "submitted")
      .order("submitted_at", { ascending: true });
    if (queryError) setError("Payment submissions could not be loaded with the current access policy.");
    else { setRows((data ?? []) as unknown as PaymentRow[]); setError(null); }
    setLoading(false);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useClinicRealtime(staff.clinicId, ["payments", "booking_requests"], load);

  async function approve(row: PaymentRow) {
    setBusyId(row.id);
    try {
      await apiRequest(`/api/payments/${row.id}/approve`, { method: "POST", body: JSON.stringify({ amount_verified: row.amount_expected ?? 0 }) });
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Payment approval failed"); }
    finally { setBusyId(null); }
  }

  async function reject(row: PaymentRow) {
    if (!reason.trim()) { setError("A rejection reason is required."); return; }
    setBusyId(row.id);
    try {
      await apiRequest(`/api/payments/${row.id}/reject`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
      setRejectingId(null); setReason(""); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Payment rejection failed"); }
    finally { setBusyId(null); }
  }

  async function openProof(row: PaymentRow) {
    if (!row.proof_storage_path) return;
    const { data, error: signedError } = await createClient().storage.from("payment-proofs").createSignedUrl(row.proof_storage_path, 300);
    if (signedError || !data.signedUrl) { setError("Payment proof access was denied or unavailable."); return; }
    window.open(data.signedUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <>
      <PageHeader eyebrow="Payment control" title="Pending verification" description="Private proof access and protected payment decisions for authorized staff." />
      <section className="panel">
        {loading ? <LoadingState label="Loading submitted payment proofs…" /> : null}
        {!loading && error ? <ErrorState message={error} retry={load} /> : null}
        {!loading && !error && rows.length === 0 ? <EmptyState title="No payment proofs require verification" message="Submitted proof attempts will appear here." /> : null}
        {!loading && rows.length > 0 ? <div className="table-wrap"><table className="data-table">
          <thead><tr><th>Submitted</th><th>Patient</th><th>Reference</th><th>Service</th><th>Expected</th><th>Status</th><th>Proof</th><th>Decision</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id}>
            <td data-label="Submitted">{row.submitted_at ? new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.submitted_at)) : "—"}</td>
            <td data-label="Patient">{row.booking_requests?.patients?.full_name ?? "—"}</td>
            <td data-label="Reference">{row.booking_requests?.patients?.patient_number ?? "—"}</td>
            <td data-label="Service">{row.booking_requests?.services?.name ?? "—"}</td>
            <td data-label="Expected">{row.currency} {Number(row.amount_expected ?? 0).toFixed(2)}</td>
            <td data-label="Status"><StatusBadge status={row.status} /></td>
            <td data-label="Proof"><button className="secondary-button" disabled={!row.proof_storage_path} onClick={() => void openProof(row)}><ExternalLink size={14} /> View</button></td>
            <td data-label="Decision">
              {rejectingId === row.id ? <div className="field" style={{ minWidth: 220 }}><input aria-label="Rejection reason" placeholder="Required rejection reason" value={reason} onChange={(event) => setReason(event.target.value)} /><div className="toolbar"><button className="danger-button" disabled={busyId === row.id || !reason.trim()} onClick={() => void reject(row)}>Confirm reject</button><button className="secondary-button" onClick={() => setRejectingId(null)}>Close</button></div></div> : <div className="toolbar"><button className="primary-button" disabled={busyId === row.id} onClick={() => void approve(row)}>Approve</button><button className="danger-button" disabled={busyId === row.id} onClick={() => setRejectingId(row.id)}>Reject</button></div>}
            </td>
          </tr>)}</tbody>
        </table></div> : null}
      </section>
    </>
  );
}
