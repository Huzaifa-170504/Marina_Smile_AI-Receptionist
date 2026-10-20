import type { ResourceConfig } from "@/components/resource-table-page";
import type { StaffRole } from "@/types/domain";

export type AuthorizedResource = ResourceConfig & { roles: StaffRole[] };

const APPOINTMENT_SELECT = "id,start_at,end_at,status,patients(patient_number,full_name),dentists(name),services(name),appointment_visits(queue_status)";

export const RESOURCE_CONFIGS: Record<string, AuthorizedResource> = {
  appointments: {
    roles: ["owner", "receptionist"], table: "appointments", select: APPOINTMENT_SELECT,
    eyebrow: "Clinic operations", title: "Appointments", description: "Confirmed, completed, cancelled, and no-show visits.",
    emptyTitle: "No appointments found", emptyMessage: "Appointments allowed by your access policy will appear here.", orderBy: "start_at", ascending: false, realtime: ["appointments", "appointment_visits"],
    backendPending: "Booking, reschedule, and cancellation actions are intentionally disabled until the Python services use the new payment-gated database contract.",
    columns: [
      { label: "Date and time", path: "start_at", kind: "date" }, { label: "Patient", path: "patients.full_name" },
      { label: "Patient no.", path: "patients.patient_number" }, { label: "Doctor", path: "dentists.name" },
      { label: "Service", path: "services.name" }, { label: "Appointment", path: "status", kind: "status" },
      { label: "Queue", path: "appointment_visits.queue_status", kind: "status" },
    ],
  },
  "booking-requests": {
    roles: ["owner", "receptionist"], table: "booking_requests", select: "id,status,preferred_date,created_at,patients(patient_number,full_name),services(name),dentists(name)",
    eyebrow: "Patient intake", title: "Booking requests", description: "Payment-gated requests that do not reserve a calendar slot.",
    emptyTitle: "No booking requests", emptyMessage: "New patient booking workflows will appear here.", orderBy: "created_at", realtime: ["booking_requests"],
    backendPending: "Creating and converting booking requests is backend-pending; unpaid requests never enter the doctor queue.",
    columns: [
      { label: "Created", path: "created_at", kind: "date" }, { label: "Patient", path: "patients.full_name" },
      { label: "Patient no.", path: "patients.patient_number" }, { label: "Service", path: "services.name" },
      { label: "Preferred doctor", path: "dentists.name" }, { label: "Status", path: "status", kind: "status" },
    ],
  },
  patients: {
    roles: ["owner", "receptionist"], table: "patients", select: "id,patient_number,full_name,date_of_birth,age_at_registration,gender,active,contacts(phone_number,name)",
    eyebrow: "Patient records", title: "Patients", description: "Clinic patients visible under your current role and RLS policy.",
    emptyTitle: "No patients found", emptyMessage: "Patient records will appear after intake.", orderBy: "full_name", ascending: true,
    columns: [
      { label: "Patient no.", path: "patient_number" }, { label: "Name", path: "full_name" },
      { label: "DOB", path: "date_of_birth", kind: "date" }, { label: "Age", path: "age_at_registration" },
      { label: "Gender", path: "gender" }, { label: "Contact", path: "contacts.phone_number" }, { label: "State", path: "active", kind: "boolean" },
    ],
  },
  doctors: {
    roles: ["owner", "receptionist"], table: "dentists", select: "id,name,specialty,bio,active,dentist_services(services(name)),dentist_schedules(weekday,start_time,end_time,active)",
    eyebrow: "Clinical team", title: "Doctors", description: "Clinical resources, assigned services, and working schedules.",
    emptyTitle: "No doctors found", emptyMessage: "Active and inactive doctors will appear here.", orderBy: "name", ascending: true,
    backendPending: "Doctor edits remain read-only. New doctors use the protected onboarding workflow, and deactivation uses the approved lifecycle check.",
    columns: [
      { label: "Doctor", path: "name" }, { label: "Specialty", path: "specialty" },
      { label: "Active", path: "active", kind: "boolean" }, { label: "Services", path: "dentist_services.services.name" },
    ],
  },
  services: {
    roles: ["owner"], table: "services", select: "id,name,description,duration_minutes,price_from,currency,payment_required,booking_deposit_amount,active,dentist_services(dentists(name))",
    eyebrow: "Clinic catalogue", title: "Services", description: "Structured operational prices, duration, payment rules, and doctor assignments.",
    emptyTitle: "No services found", emptyMessage: "Clinic services will appear here.", orderBy: "name", ascending: true,
    backendPending: "Service create/edit controls require a protected backend endpoint before they can be enabled.",
    columns: [
      { label: "Service", path: "name" }, { label: "Duration", path: "duration_minutes" },
      { label: "Price from", path: "price_from", kind: "money" }, { label: "Deposit", path: "booking_deposit_amount", kind: "money" },
      { label: "Payment required", path: "payment_required", kind: "boolean" }, { label: "Active", path: "active", kind: "boolean" },
    ],
  },
  schedule: {
    roles: ["owner", "receptionist"], table: "dentist_schedules", select: "id,weekday,start_time,end_time,active,dentists(name)",
    eyebrow: "Availability", title: "Weekly schedule", description: "Base working hours. Overrides are shown separately from appointments.",
    emptyTitle: "No weekly hours", emptyMessage: "Working hours will appear here.", orderBy: "weekday", ascending: true,
    backendPending: "Schedule and override form submissions remain disabled until protected backend validation endpoints are available.",
    columns: [
      { label: "Doctor", path: "dentists.name" }, { label: "Weekday", path: "weekday" },
      { label: "Starts", path: "start_time" }, { label: "Ends", path: "end_time" }, { label: "Active", path: "active", kind: "boolean" },
    ],
  },
  handoffs: {
    roles: ["owner", "receptionist"], table: "handoffs", select: "id,reason,summary,priority,status,created_at,resolved_at,contacts(name,phone_number),staff_users(full_name)",
    eyebrow: "Human support", title: "Handoffs", description: "Cases requiring clinic staff attention.",
    emptyTitle: "No open handoffs", emptyMessage: "Human escalation cases will appear here.", orderBy: "created_at", realtime: ["handoffs"],
    backendPending: "Assignment and resolution mutations require a protected handoff service before controls are enabled.",
    columns: [
      { label: "Created", path: "created_at", kind: "date" }, { label: "Contact", path: "contacts.name" },
      { label: "Reason", path: "reason" }, { label: "Priority", path: "priority", kind: "status" },
      { label: "Assigned to", path: "staff_users.full_name" }, { label: "Status", path: "status", kind: "status" },
    ],
  },
  audit: {
    roles: ["owner"], table: "appointment_audit_log", select: "id,action,actor_type,reason,created_at,appointment_id,patient_id",
    eyebrow: "Accountability", title: "Audit trail", description: "Append-only appointment and payment lifecycle actions.",
    emptyTitle: "No audit activity", emptyMessage: "Protected domain actions will be recorded here.", orderBy: "created_at",
    columns: [
      { label: "Time", path: "created_at", kind: "date" }, { label: "Action", path: "action" },
      { label: "Actor", path: "actor_type" }, { label: "Reason", path: "reason" }, { label: "Appointment", path: "appointment_id" },
    ],
  },
  settings: {
    roles: ["owner"], table: "clinics", select: "id,name,slug,business_type,timezone,address,emergency_phone,languages,active",
    eyebrow: "Configuration", title: "Clinic settings", description: "Non-secret clinic identity and operating configuration.",
    emptyTitle: "Clinic unavailable", emptyMessage: "The linked clinic could not be found.",
    backendPending: "Integration identifiers and credentials are intentionally excluded. Configuration writes require a protected clinic-management endpoint.",
    columns: [
      { label: "Clinic", path: "name" }, { label: "Timezone", path: "timezone" }, { label: "Address", path: "address" },
      { label: "Emergency phone", path: "emergency_phone" }, { label: "Languages", path: "languages" }, { label: "Active", path: "active", kind: "boolean" },
    ],
  },
  "my-calendar": {
    roles: ["doctor"], table: "appointments", select: APPOINTMENT_SELECT,
    eyebrow: "My clinical day", title: "My calendar", description: "Appointments assigned to your doctor profile.",
    emptyTitle: "No appointments", emptyMessage: "Your assigned schedule is clear.", orderBy: "start_at", ascending: true, realtime: ["appointments"],
    columns: [
      { label: "Date and time", path: "start_at", kind: "date" }, { label: "Patient", path: "patients.full_name" },
      { label: "Service", path: "services.name" }, { label: "Status", path: "status", kind: "status" },
    ],
  },
  "my-appointments": {
    roles: ["doctor"], table: "appointments", select: APPOINTMENT_SELECT,
    eyebrow: "Clinical history", title: "My appointments", description: "Appointment history limited to your assigned patients.",
    emptyTitle: "No appointments", emptyMessage: "Assigned appointments will appear here.", orderBy: "start_at", realtime: ["appointments", "appointment_visits"],
    backendPending: "Reschedule and cancellation controls are backend-pending and cannot directly modify appointment times or status.",
    columns: [
      { label: "Date and time", path: "start_at", kind: "date" }, { label: "Patient", path: "patients.full_name" },
      { label: "Service", path: "services.name" }, { label: "Appointment", path: "status", kind: "status" }, { label: "Queue", path: "appointment_visits.queue_status", kind: "status" },
    ],
  },
  "my-patients": {
    roles: ["doctor"], table: "patients", select: "id,patient_number,full_name,date_of_birth,age_at_registration,gender,active,contacts(name,phone_number)",
    eyebrow: "Assigned care", title: "My patients", description: "Only patients connected to your appointments are visible.",
    emptyTitle: "No assigned patients", emptyMessage: "Patients linked to your appointments will appear here.", orderBy: "full_name", ascending: true,
    columns: [
      { label: "Patient no.", path: "patient_number" }, { label: "Name", path: "full_name" },
      { label: "DOB", path: "date_of_birth", kind: "date" }, { label: "Gender", path: "gender" }, { label: "Contact", path: "contacts.phone_number" },
    ],
  },
  "my-availability": {
    roles: ["doctor"], table: "schedule_overrides", select: "id,start_at,end_at,override_type,reason,created_at,dentists(name)",
    eyebrow: "Working time", title: "My availability", description: "Upcoming unavailable blocks and extra availability.",
    emptyTitle: "No schedule overrides", emptyMessage: "Your base weekly schedule remains in effect.", orderBy: "start_at", ascending: true, realtime: ["schedule_overrides"],
    backendPending: "Submitting leave or availability changes requires a protected schedule endpoint before controls are enabled.",
    columns: [
      { label: "Starts", path: "start_at", kind: "date" }, { label: "Ends", path: "end_at", kind: "date" },
      { label: "Type", path: "override_type", kind: "status" }, { label: "Reason", path: "reason" },
    ],
  },
};
