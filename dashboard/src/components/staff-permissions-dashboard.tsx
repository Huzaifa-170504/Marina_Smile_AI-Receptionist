"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { apiRequest } from "@/lib/api";
import {
  PERMISSION_DEFINITIONS, ROLE_DESCRIPTIONS, roleDefaultPermissions, roleLabel,
  type PermissionSection,
} from "@/lib/permissions";
import type { Permission, PermissionOverride, StaffRole } from "@/types/domain";

type AccountSetupStatus = "invited" | "account_configured" | "invitation_problem";
type StaffRow = {
  id: string; full_name: string; email: string; role: StaffRole; active: boolean;
  doctor_id: string | null; access_status: "active" | "inactive";
  account_setup_status: AccountSetupStatus; staff_permission_overrides: PermissionOverride[];
};

const sections = [...new Set(PERMISSION_DEFINITIONS.map((item) => item.section))] as PermissionSection[];

export function StaffPermissionsDashboard() {
  const [rows, setRows] = useState<StaffRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const records = await apiRequest<StaffRow[]>("/api/staff");
      setRows(records); setSelectedId((current) => current ?? records[0]?.id ?? null); setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Staff access data could not be loaded."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  const selected = rows.find((row) => row.id === selectedId) ?? null;

  async function mutate(key: string, operation: () => Promise<unknown>, success?: string) {
    setBusy(key); setError(null); setNotice(null);
    try { await operation(); if (success) setNotice(success); await load(); return true; }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The account update failed."); return false; }
    finally { setBusy(null); }
  }

  async function inviteStaff(formData: FormData) {
    const succeeded = await mutate("invite", () => apiRequest("/api/staff/invite", { method: "POST", body: JSON.stringify({
      full_name: String(formData.get("full_name") ?? ""), email: String(formData.get("email") ?? ""),
      role: String(formData.get("role") ?? "receptionist"), active: formData.get("active") === "on",
    }) }), "Invitation sent securely.");
    if (succeeded) setInviteOpen(false);
  }

  async function setOverride(permission: Permission, allowed: boolean | null) {
    if (!selected) return;
    await mutate(`permission:${permission}`, () => apiRequest(`/api/staff/${selected.id}/permissions`, {
      method: "PUT", body: JSON.stringify({ permission, allowed }),
    }));
  }

  return <>
    <PageHeader eyebrow="Owner administration" title="Staff & Access" description="Roles provide sensible access by default. Explicit exceptions are only needed for individual staff members." actions={<button className="primary-button" onClick={() => setInviteOpen((value) => !value)}>{inviteOpen ? "Close" : "Add Staff Member"}</button>} />
    <section className="panel role-defaults"><div className="panel-header"><div><h2>Role defaults</h2><p className="muted">These permissions are inherited automatically; you do not need to configure each one.</p></div></div>
      <div className="role-default-grid">{(["owner", "doctor", "receptionist", "payment_verifier"] as StaffRole[]).map((role) => <div className="role-default-card" key={role}><strong>{roleLabel(role)}</strong><p className="muted">{ROLE_DESCRIPTIONS[role]}</p></div>)}</div>
    </section>
    {inviteOpen ? <section className="panel"><div className="panel-header"><div><h2>Add Staff Member</h2><p className="muted">Doctors include clinical setup and must be added from the Doctors page.</p></div><Link className="secondary-button" href="/dashboard/doctors">Add Doctor</Link></div>
      <form className="form-grid" action={(data) => void inviteStaff(data)}><div className="two-column"><div className="field"><label htmlFor="invite-name">Name</label><input id="invite-name" name="full_name" required /></div><div className="field"><label htmlFor="invite-email">Email</label><input id="invite-email" name="email" type="email" required /></div></div>
        <div className="two-column"><div className="field"><label htmlFor="invite-role">Role</label><select id="invite-role" name="role" defaultValue="receptionist"><option value="owner">Owner</option><option value="receptionist">Receptionist</option><option value="payment_verifier">Payment Verifier</option></select></div><label className="check-field"><input name="active" type="checkbox" defaultChecked /> Allow dashboard access immediately</label></div>
        <button className="primary-button" disabled={busy === "invite"}>{busy === "invite" ? "Sending invitation…" : "Save & Send Invitation"}</button></form>
    </section> : null}
    {notice ? <p className="notice" role="status">{notice}</p> : null}{error ? <ErrorState message={error} retry={load} /> : null}
    <div className="two-column staff-access-layout"><section className="panel"><div className="panel-header"><h2>Clinic staff</h2></div>
      {loading ? <LoadingState /> : null}{!loading && !error && rows.length === 0 ? <EmptyState title="No staff accounts" message="Add a staff member to send a secure invitation." /> : null}
      {!loading && rows.length ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Access status</th><th>Account setup</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id} className={selectedId === row.id ? "selected-row" : ""} onClick={() => setSelectedId(row.id)}><td>{row.full_name}</td><td>{row.email}</td><td>{roleLabel(row.role)}</td><td><StatusBadge status={row.access_status} /></td><td><StatusBadge status={row.account_setup_status} /></td><td><div className="toolbar"><button className="secondary-button" onClick={(event) => { event.stopPropagation(); setSelectedId(row.id); }}>Manage access</button>{row.account_setup_status !== "account_configured" ? <button className="secondary-button" disabled={busy === `resend:${row.id}`} onClick={(event) => { event.stopPropagation(); void mutate(`resend:${row.id}`, () => apiRequest(`/api/staff/${row.id}/resend-invitation`, { method: "POST" }), "A fresh setup link was sent."); }}>Resend invitation</button> : null}</div></td></tr>)}</tbody></table></div> : null}
    </section>
    <section className="panel"><div className="panel-header"><div><h2>{selected ? `Manage access: ${selected.full_name}` : "Manage access"}</h2>{selected ? <p className="muted">Role: {roleLabel(selected.role)} · {ROLE_DESCRIPTIONS[selected.role]}</p> : null}</div></div>
      {selected ? <div className="stack"><div className="toolbar access-actions"><button className={selected.active ? "danger-button" : "primary-button"} disabled={busy === "active"} onClick={() => void mutate("active", () => apiRequest(`/api/staff/${selected.id}/active`, { method: "PATCH", body: JSON.stringify({ active: !selected.active }) }))}>{selected.active ? "Deactivate access" : "Activate access"}</button>{selected.role !== "doctor" ? <select aria-label="Staff role" value={selected.role} disabled={busy === "role"} onChange={(event) => void mutate("role", () => apiRequest(`/api/staff/${selected.id}/role`, { method: "PATCH", body: JSON.stringify({ role: event.target.value }) }))}><option value="owner">Owner</option><option value="receptionist">Receptionist</option><option value="payment_verifier">Payment Verifier</option></select> : <span className="muted">Doctor role is managed through the doctor lifecycle.</span>}</div>
        {sections.map((section) => <div className="permission-section" key={section}><h3>{section}</h3>{PERMISSION_DEFINITIONS.filter((item) => item.section === section).map((definition) => {
          const inherited = roleDefaultPermissions(selected.role).includes(definition.permission);
          const override = selected.staff_permission_overrides.find((item) => item.permission === definition.permission);
          const state = override ? `Explicitly ${override.allowed ? "allowed" : "denied"}` : `Inherited from ${roleLabel(selected.role)} role`;
          return <div className="permission-row" key={definition.permission}><div><strong>{definition.label}</strong><p className="muted">{definition.description}</p><span className={`access-state ${override ? (override.allowed ? "allowed" : "denied") : "inherited"}`}>{state}</span></div><div className="toolbar"><button className="secondary-button" disabled={busy === `permission:${definition.permission}` || (!override && inherited)} onClick={() => void setOverride(definition.permission, true)}>Allow</button><button className="danger-button" disabled={busy === `permission:${definition.permission}` || (!override && !inherited)} onClick={() => void setOverride(definition.permission, false)}>Deny</button><button className="secondary-button" disabled={busy === `permission:${definition.permission}` || !override} onClick={() => void setOverride(definition.permission, null)}>Use Role Default</button></div></div>;
        })}</div>)}</div> : <EmptyState title="Select a staff member" message="Choose an employee to review role defaults and explicit exceptions." />}
    </section></div>
  </>;
}
