"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { createClient } from "@/lib/supabase/browser";

type ServiceRow = {
  id: string; name: string; description: string | null; duration_minutes: number;
  price_from: number | null; currency: string; payment_required: boolean;
  booking_deposit_amount: number | null; active: boolean;
};

const EMPTY_FORM = { id: "", name: "", description: "", duration: "30", price: "", currency: "AED", paymentRequired: true, deposit: "", active: true };

export function ServicesManager() {
  const staff = useStaff();
  const [rows, setRows] = useState<ServiceRow[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const { data, error: queryError } = await createClient().from("services").select("id,name,description,duration_minutes,price_from,currency,payment_required,booking_deposit_amount,active").order("name");
    if (queryError) setError("Services could not be loaded."); else { setRows((data ?? []) as ServiceRow[]); setError(null); }
    setLoading(false);
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  function edit(row: ServiceRow) {
    setForm({ id: row.id, name: row.name, description: row.description ?? "", duration: String(row.duration_minutes), price: row.price_from === null ? "" : String(row.price_from), currency: row.currency, paymentRequired: row.payment_required, deposit: row.booking_deposit_amount === null ? "" : String(row.booking_deposit_amount), active: row.active });
    setShowForm(true);
  }

  async function save() {
    if (!form.name.trim() || Number(form.duration) <= 0 || (form.price !== "" && Number(form.price) < 0) || (form.deposit !== "" && Number(form.deposit) < 0) || !/^[A-Z]{3}$/.test(form.currency)) { setError("Name, positive duration, non-negative amounts, and a three-letter currency are required."); return; }
    setSaving(true); setError(null);
    const payload = {
      clinic_id: staff.clinicId,
      name: form.name.trim(), description: form.description.trim() || null,
      duration_minutes: Number(form.duration), price_from: form.price === "" ? null : Number(form.price),
      currency: form.currency, payment_required: form.paymentRequired,
      booking_deposit_amount: form.deposit === "" ? null : Number(form.deposit), active: form.active,
    };
    const supabase = createClient();
    const result = form.id ? await supabase.from("services").update(payload).eq("id", form.id) : await supabase.from("services").insert(payload);
    if (result.error) setError("The service could not be saved under the current RLS policy.");
    else { setShowForm(false); setForm(EMPTY_FORM); await load(); }
    setSaving(false);
  }

  async function toggle(row: ServiceRow) {
    const { error: updateError } = await createClient().from("services").update({ active: !row.active }).eq("id", row.id);
    if (updateError) setError("The service state could not be changed."); else await load();
  }

  return <>
    <PageHeader eyebrow="Clinic catalogue" title="Services" description="Structured duration, prices, payment requirements, deposits, and active state." actions={<button className="primary-button" onClick={() => { setForm(EMPTY_FORM); setShowForm((value) => !value); }}>{showForm ? "Close form" : "Add service"}</button>} />
    {showForm ? <section className="panel"><div className="panel-header"><h2>{form.id ? "Edit service" : "Add service"}</h2></div><div className="form-grid">
      <div className="two-column"><div className="field"><label>Name</label><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div><div className="field"><label>Description</label><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></div></div>
      <div className="two-column"><div className="field"><label>Duration (minutes)</label><input type="number" min="1" value={form.duration} onChange={(event) => setForm({ ...form, duration: event.target.value })} /></div><div className="field"><label>Currency</label><input maxLength={3} value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value.toUpperCase() })} /></div></div>
      <div className="two-column"><div className="field"><label>Price from</label><input type="number" min="0" step="0.01" value={form.price} onChange={(event) => setForm({ ...form, price: event.target.value })} /></div><div className="field"><label>Booking deposit</label><input type="number" min="0" step="0.01" value={form.deposit} onChange={(event) => setForm({ ...form, deposit: event.target.value })} /></div></div>
      <div className="toolbar"><label><input type="checkbox" checked={form.paymentRequired} onChange={(event) => setForm({ ...form, paymentRequired: event.target.checked })} /> Payment required</label><label><input type="checkbox" checked={form.active} onChange={(event) => setForm({ ...form, active: event.target.checked })} /> Active</label></div>
      <button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save service"}</button>
    </div></section> : null}
    <section className="panel">{loading ? <LoadingState /> : null}{!loading && error ? <ErrorState message={error} retry={load} /> : null}{!loading && !error && rows.length === 0 ? <EmptyState title="No services found" message="Add the first structured clinic service." /> : null}{!loading && rows.length > 0 ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Service</th><th>Duration</th><th>Price</th><th>Deposit</th><th>Payment</th><th>State</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td data-label="Service"><strong>{row.name}</strong><br /><small className="muted">{row.description}</small></td><td data-label="Duration">{row.duration_minutes} min</td><td data-label="Price">{row.currency} {Number(row.price_from ?? 0).toFixed(2)}</td><td data-label="Deposit">{row.booking_deposit_amount === null ? "—" : `${row.currency} ${Number(row.booking_deposit_amount).toFixed(2)}`}</td><td data-label="Payment"><StatusBadge status={row.payment_required ? "required" : "not_required"} /></td><td data-label="State"><StatusBadge status={row.active ? "active" : "inactive"} /></td><td data-label="Actions"><div className="toolbar"><button className="secondary-button" onClick={() => edit(row)}>Edit</button><button className={row.active ? "danger-button" : "secondary-button"} onClick={() => void toggle(row)}>{row.active ? "Deactivate" : "Activate"}</button></div></td></tr>)}</tbody></table></div> : null}</section>
  </>;
}
