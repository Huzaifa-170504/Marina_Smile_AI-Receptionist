import { StaffPermissionsDashboard } from "@/components/staff-permissions-dashboard";
import { getStaffProfile } from "@/lib/auth";
import { notFound } from "next/navigation";

export default async function StaffPage() {
  const staff = await getStaffProfile();
  if (!staff.permissions.includes("staff.manage")) notFound();
  return <StaffPermissionsDashboard />;
}
