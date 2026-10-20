import type { Permission, PermissionOverride, StaffRole } from "@/types/domain";

export const KNOWN_PERMISSIONS: Permission[] = [
  "appointment.view_all", "appointment.view_own", "appointment.create",
  "appointment.reschedule_any", "appointment.reschedule_own",
  "appointment.cancel_any", "appointment.cancel_own", "queue.view_all",
  "queue.view_own", "queue.update_all", "queue.update_own",
  "schedule.manage_any", "schedule.manage_own", "payment.verify",
  "payment.view_proof", "patient.view_all", "patient.view_assigned",
  "doctor.manage", "service.manage", "staff.manage", "clinic.manage",
];

export type PermissionSection =
  | "Appointments" | "Patients" | "Queues" | "Scheduling" | "Payments"
  | "Doctors & Services" | "Staff Administration" | "Clinic Administration";

export type PermissionDefinition = {
  permission: Permission;
  label: string;
  description: string;
  section: PermissionSection;
};

export const PERMISSION_DEFINITIONS: PermissionDefinition[] = [
  { permission: "appointment.view_own", label: "View own appointments", description: "See appointments assigned to this doctor.", section: "Appointments" },
  { permission: "appointment.view_all", label: "View all clinic appointments", description: "See appointments across the clinic.", section: "Appointments" },
  { permission: "appointment.create", label: "Create appointments", description: "Create appointments for clinic patients.", section: "Appointments" },
  { permission: "appointment.reschedule_own", label: "Reschedule own appointments", description: "Reschedule appointments assigned to this doctor.", section: "Appointments" },
  { permission: "appointment.reschedule_any", label: "Reschedule any appointment", description: "Reschedule appointments across the clinic.", section: "Appointments" },
  { permission: "appointment.cancel_own", label: "Cancel own appointments", description: "Cancel appointments assigned to this doctor.", section: "Appointments" },
  { permission: "appointment.cancel_any", label: "Cancel any appointment", description: "Cancel appointments across the clinic.", section: "Appointments" },
  { permission: "patient.view_assigned", label: "View assigned patients", description: "See patients connected to this doctor's appointments.", section: "Patients" },
  { permission: "patient.view_all", label: "View all clinic patients", description: "See patient records across the clinic.", section: "Patients" },
  { permission: "queue.view_own", label: "View own patient queue", description: "See this doctor's patient queue.", section: "Queues" },
  { permission: "queue.view_all", label: "View all patient queues", description: "See queues for all clinic doctors.", section: "Queues" },
  { permission: "queue.update_own", label: "Manage own patient queue", description: "Move patients through this doctor's queue.", section: "Queues" },
  { permission: "queue.update_all", label: "Manage all patient queues", description: "Move patients through any clinic queue.", section: "Queues" },
  { permission: "schedule.manage_own", label: "Manage own availability", description: "Maintain this doctor's availability.", section: "Scheduling" },
  { permission: "schedule.manage_any", label: "Manage all doctor schedules", description: "Maintain schedules and availability for all doctors.", section: "Scheduling" },
  { permission: "payment.verify", label: "Verify submitted payments", description: "Approve or reject submitted payments.", section: "Payments" },
  { permission: "payment.view_proof", label: "View payment proof screenshots", description: "Open private payment proof files.", section: "Payments" },
  { permission: "doctor.manage", label: "Manage doctors", description: "Create and administer doctor records.", section: "Doctors & Services" },
  { permission: "service.manage", label: "Manage services", description: "Maintain clinic services, pricing, and assignments.", section: "Doctors & Services" },
  { permission: "staff.manage", label: "Manage staff and access", description: "Invite staff and manage roles and access exceptions.", section: "Staff Administration" },
  { permission: "clinic.manage", label: "Manage clinic settings", description: "Maintain clinic-wide configuration.", section: "Clinic Administration" },
];

export const ROLE_DESCRIPTIONS: Record<StaffRole, string> = {
  owner: "Full clinic administration",
  doctor: "Own appointments, patient queue, assigned patients, and availability",
  receptionist: "Clinic appointments, patients, queues, schedules, and handoffs",
  payment_verifier: "Submitted payments, proof review, and approval or rejection",
};

const ROLE_DEFAULTS: Record<StaffRole, Permission[]> = {
  owner: KNOWN_PERMISSIONS,
  doctor: [
    "appointment.view_own", "appointment.reschedule_own",
    "appointment.cancel_own", "queue.view_own", "queue.update_own",
    "schedule.manage_own", "patient.view_assigned",
  ],
  receptionist: [
    "appointment.view_all", "appointment.create", "appointment.reschedule_any",
    "appointment.cancel_any", "queue.view_all", "queue.update_all",
    "schedule.manage_any", "patient.view_all",
  ],
  payment_verifier: ["payment.verify", "payment.view_proof"],
};

export function roleDefaultPermissions(role: StaffRole): Permission[] {
  return [...ROLE_DEFAULTS[role]];
}

export function resolvePermissions(
  role: StaffRole,
  overrides: PermissionOverride[],
): Permission[] {
  const permissions = new Set(ROLE_DEFAULTS[role]);
  overrides.forEach(({ permission, allowed }) => {
    if (allowed) permissions.add(permission);
    else permissions.delete(permission);
  });
  return [...permissions];
}

export function roleLabel(role: StaffRole): string {
  return {
    owner: "Owner",
    doctor: "Doctor",
    receptionist: "Receptionist",
    payment_verifier: "Payment verifier",
  }[role];
}
