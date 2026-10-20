export type StaffRole = "owner" | "doctor" | "receptionist" | "payment_verifier";

export type Permission =
  | "appointment.view_all"
  | "appointment.view_own"
  | "appointment.create"
  | "appointment.reschedule_any"
  | "appointment.reschedule_own"
  | "appointment.cancel_any"
  | "appointment.cancel_own"
  | "queue.view_all"
  | "queue.view_own"
  | "queue.update_all"
  | "queue.update_own"
  | "schedule.manage_any"
  | "schedule.manage_own"
  | "payment.verify"
  | "payment.view_proof"
  | "patient.view_all"
  | "patient.view_assigned"
  | "doctor.manage"
  | "service.manage"
  | "staff.manage"
  | "clinic.manage";

export type AppointmentStatus = "pending" | "confirmed" | "cancelled" | "completed" | "no_show";
export type QueueStatus = "scheduled" | "checked_in" | "waiting" | "in_progress" | "completed" | "no_show";
export type PaymentStatus = "awaiting" | "submitted" | "verified" | "rejected" | "refund_required" | "refund_processing" | "refunded";
export type BookingRequestStatus = "awaiting_patient_info" | "awaiting_payment" | "payment_submitted" | "payment_rejected" | "payment_verified" | "awaiting_slot_selection" | "converted_to_appointment" | "expired" | "cancelled";

export type PermissionOverride = {
  permission: Permission;
  allowed: boolean;
};

export type StaffProfile = {
  id: string;
  clinicId: string;
  authUserId: string;
  doctorId: string | null;
  fullName: string;
  email: string;
  role: StaffRole;
  active: boolean;
  permissions: Permission[];
  overrides: PermissionOverride[];
};

export type OverviewMetrics = {
  todaysAppointments: number;
  waitingPatients: number;
  inProgressPatients: number;
  completedToday: number;
  upcomingAppointments: number;
  pendingPayments: number;
  todaysCancellations: number;
  noShowsToday: number;
};

export type ApiErrorPayload = {
  detail?: string;
  message?: string;
};
