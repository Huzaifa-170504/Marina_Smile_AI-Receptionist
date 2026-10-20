"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { ResourceTablePage } from "@/components/resource-table-page";
import { useStaff } from "@/components/staff-context";
import { apiRequest } from "@/lib/api";
import { RESOURCE_CONFIGS } from "@/lib/resource-config";
import { createClient } from "@/lib/supabase/browser";

type DoctorOption = { id: string; name: string; active: boolean };
type ServiceOption = { id: string; name: string };
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function DoctorsDashboard() {
  const staff = useStaff();
  const canOnboardDoctor = staff.permissions.includes("doctor.manage") && staff.permissions.includes("staff.manage");
  const [doctors, setDoctors] = useState<DoctorOption[]>([]);
  const [doctorId, setDoctorId] = useState("");
  const [reason, setReason] = useState("");
  const [onboardingMessage, setOnboardingMessage] = useState<string | null>(null);
  const [deactivationMessage, setDeactivationMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [services, setServices] = useState<ServiceOption[]>([]);
  const onboardingId = useRef<string | null>(null);

  const loadDoctors = useCallback(async () => {
    const client = createClient();
    const { data } = await client.from("dentists").select("id,name,active").eq("active", true).order("name");
    setDoctors((data ?? []) as DoctorOption[]);
    const { data: serviceRows } = await client.from("services").select("id,name").eq("active", true).order("name");
    setServices((serviceRows ?? []) as ServiceOption[]);
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void loadDoctors(), 0); return () => window.clearTimeout(timer); }, [loadDoctors]);

  async function addDoctor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setOnboardingMessage(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const schedule = WEEKDAYS.flatMap((_label, weekday) => form.get(`schedule_${weekday}`) === "on" ? [{
      weekday,
      start_time: String(form.get(`start_${weekday}`) ?? ""),
      end_time: String(form.get(`end_${weekday}`) ?? ""),
    }] : []);
    try {
      const result = await apiRequest<{ email: string }>("/api/doctors", { method: "POST", body: JSON.stringify({
        onboarding_id: onboardingId.current ??= crypto.randomUUID(), full_name: String(form.get("full_name") ?? ""),
        email: String(form.get("email") ?? ""), specialty: String(form.get("specialty") ?? ""),
        bio: String(form.get("bio") ?? ""), service_ids: form.getAll("service_ids").map(String),
        weekly_schedule: schedule, active: form.get("active") === "on",
      }) });
      setOnboardingMessage(`Doctor added. An account setup link was sent to ${result.email}.`);
      formElement.reset(); onboardingId.current = null; setAddOpen(false); await loadDoctors();
    } catch (caught) { setOnboardingMessage(caught instanceof Error ? caught.message : "Doctor onboarding failed safely."); }
    finally { setPending(false); }
  }

  async function deactivate() {
    if (!doctorId || !reason.trim()) { setDeactivationMessage("Select a doctor and provide a reason."); return; }
    setPending(true); setDeactivationMessage(null);
    try {
      await apiRequest(`/api/doctors/${doctorId}/deactivate`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
      setDeactivationMessage("Doctor deactivated after the protected workflow completed."); setDoctorId(""); setReason(""); await loadDoctors();
    } catch (caught) { setDeactivationMessage(caught instanceof Error ? caught.message : "Doctor deactivation failed"); }
    finally { setPending(false); }
  }

  return (
    <div className="stack">
      {canOnboardDoctor ? <section className="panel"><div className="panel-header"><div><h2>Add Doctor</h2><p className="muted">Create the clinical record, services, weekly schedule, and staff invitation together.</p></div><button className="primary-button" onClick={() => setAddOpen((value) => !value)}>{addOpen ? "Close" : "Add Doctor"}</button></div>
        {addOpen ? <form className="form-grid" onSubmit={addDoctor}><div className="two-column"><div className="field"><label htmlFor="doctor-name">Full name</label><input id="doctor-name" name="full_name" required /></div><div className="field"><label htmlFor="doctor-email">Email</label><input id="doctor-email" name="email" type="email" required /></div></div>
          <div className="field"><label htmlFor="doctor-specialty">Specialty</label><input id="doctor-specialty" name="specialty" /></div><div className="field"><label htmlFor="doctor-bio">Bio</label><textarea id="doctor-bio" name="bio" rows={3} /></div>
          <fieldset className="choice-group"><legend>Services</legend><div className="choice-grid">{services.map((service) => <label className="check-field" key={service.id}><input type="checkbox" name="service_ids" value={service.id} /> {service.name}</label>)}</div></fieldset>
          <fieldset className="choice-group"><legend>Weekly working schedule</legend><div className="schedule-grid">{WEEKDAYS.map((day, index) => <div className="schedule-row" key={day}><label className="check-field"><input type="checkbox" name={`schedule_${index}`} defaultChecked={index < 5} /> {day}</label><input aria-label={`${day} start time`} name={`start_${index}`} type="time" defaultValue="09:00" /><span>to</span><input aria-label={`${day} end time`} name={`end_${index}`} type="time" defaultValue="17:00" /></div>)}</div></fieldset>
          <label className="check-field"><input name="active" type="checkbox" defaultChecked /> Allow dashboard access immediately after setup</label>
          <button className="primary-button" disabled={pending || services.length === 0}>{pending ? "Saving and sending…" : "Save & Send Invitation"}</button>
        </form> : null}{onboardingMessage ? <p className="notice" role="status">{onboardingMessage}</p> : null}
      </section> : null}
      <ResourceTablePage config={RESOURCE_CONFIGS.doctors} />
      {staff.permissions.includes("doctor.manage") ? <section className="panel">
        <div className="panel-header"><div><h2>Protected doctor deactivation</h2><p className="muted">The database rejects deactivation while future active appointments exist.</p></div></div>
        <div className="toolbar">
          <div className="field"><label htmlFor="doctor-deactivate">Doctor</label><select id="doctor-deactivate" value={doctorId} onChange={(event) => setDoctorId(event.target.value)}><option value="">Select doctor</option>{doctors.map((doctor) => <option key={doctor.id} value={doctor.id}>{doctor.name}</option>)}</select></div>
          <div className="field" style={{ flex: 1 }}><label htmlFor="deactivation-reason">Reason</label><input id="deactivation-reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required operational reason" /></div>
          <button className="danger-button" disabled={pending || !doctorId || !reason.trim()} onClick={() => void deactivate()}>{pending ? "Checking…" : "Deactivate safely"}</button>
        </div>
        {deactivationMessage ? <p className="notice" role="status">{deactivationMessage}</p> : null}
      </section> : null}
    </div>
  );
}
