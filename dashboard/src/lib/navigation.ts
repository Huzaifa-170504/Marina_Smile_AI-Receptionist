import {
  Activity, CalendarDays, ClipboardList, CreditCard, FileClock, Handshake,
  LayoutDashboard, Settings, Stethoscope, Users, UserRoundCog, type LucideIcon,
} from "lucide-react";

import type { StaffRole } from "@/types/domain";

export type NavigationItem = { href: string; label: string; icon: LucideIcon };

const shared = {
  overview: { href: "/dashboard", label: "Today", icon: LayoutDashboard },
  appointments: { href: "/dashboard/appointments", label: "Appointments", icon: CalendarDays },
  queue: { href: "/dashboard/queue", label: "Patient queue", icon: Activity },
  patients: { href: "/dashboard/patients", label: "Patients", icon: Users },
  doctors: { href: "/dashboard/doctors", label: "Doctors", icon: Stethoscope },
  payments: { href: "/dashboard/payments", label: "Payments", icon: CreditCard },
  schedule: { href: "/dashboard/schedule", label: "Schedule", icon: CalendarDays },
  handoffs: { href: "/dashboard/handoffs", label: "Handoffs", icon: Handshake },
};

const NAVIGATION: Record<StaffRole, NavigationItem[]> = {
  owner: [
    shared.overview, shared.appointments, shared.queue,
    { href: "/dashboard/booking-requests", label: "Booking requests", icon: ClipboardList },
    shared.patients, shared.doctors,
    { href: "/dashboard/services", label: "Services", icon: ClipboardList },
    shared.schedule, shared.payments,
    { href: "/dashboard/staff", label: "Staff & access", icon: UserRoundCog },
    shared.handoffs,
    { href: "/dashboard/audit", label: "Audit trail", icon: FileClock },
    { href: "/dashboard/settings", label: "Clinic settings", icon: Settings },
  ],
  doctor: [
    shared.overview,
    { href: "/dashboard/my-queue", label: "My queue", icon: Activity },
    { href: "/dashboard/my-calendar", label: "My calendar", icon: CalendarDays },
    { href: "/dashboard/my-appointments", label: "My appointments", icon: ClipboardList },
    { href: "/dashboard/my-patients", label: "My patients", icon: Users },
    { href: "/dashboard/my-availability", label: "My availability", icon: Stethoscope },
  ],
  receptionist: [
    shared.overview, shared.appointments, shared.queue,
    { href: "/dashboard/booking-requests", label: "Booking requests", icon: ClipboardList },
    shared.patients, shared.doctors, shared.schedule, shared.handoffs,
  ],
  payment_verifier: [shared.overview, shared.payments],
};

export function navigationForRole(role: StaffRole): NavigationItem[] {
  return NAVIGATION[role];
}
