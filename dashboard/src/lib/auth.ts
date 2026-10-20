import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";
import { notFound } from "next/navigation";

import { resolvePermissions } from "@/lib/permissions";
import { createClient } from "@/lib/supabase/server";
import type { PermissionOverride, StaffProfile, StaffRole } from "@/types/domain";

type StaffRow = {
  id: string;
  clinic_id: string;
  auth_user_id: string;
  doctor_id: string | null;
  full_name: string;
  email: string;
  role: StaffRole;
  active: boolean;
};

export const getStaffProfile = cache(async (): Promise<StaffProfile> => {
  const supabase = await createClient();
  const { data: authData, error: authError } = await supabase.auth.getUser();
  if (authError || !authData.user) redirect("/login");

  const { data: staff, error: staffError } = await supabase
    .from("staff_users")
    .select("id, clinic_id, auth_user_id, doctor_id, full_name, email, role, active")
    .eq("auth_user_id", authData.user.id)
    .maybeSingle<StaffRow>();

  if (staffError || !staff || !staff.active) redirect("/account-disabled");

  const { data: overrideRows } = await supabase
    .from("staff_permission_overrides")
    .select("permission, allowed")
    .eq("staff_user_id", staff.id);
  const overrides = (overrideRows ?? []) as PermissionOverride[];

  return {
    id: staff.id,
    clinicId: staff.clinic_id,
    authUserId: staff.auth_user_id,
    doctorId: staff.doctor_id,
    fullName: staff.full_name,
    email: staff.email,
    role: staff.role,
    active: staff.active,
    permissions: resolvePermissions(staff.role, overrides),
    overrides,
  };
});

export async function requireRole(...roles: StaffRole[]): Promise<StaffProfile> {
  const staff = await getStaffProfile();
  if (!roles.includes(staff.role)) notFound();
  return staff;
}
