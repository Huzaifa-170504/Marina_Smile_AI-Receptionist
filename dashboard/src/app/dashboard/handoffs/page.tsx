import { HandoffsDashboard } from "@/components/handoffs-dashboard";
import { getStaffProfile } from "@/lib/auth";
import { notFound } from "next/navigation";

export default async function HandoffsPage() {
  const staff = await getStaffProfile();
  if (!staff.permissions.includes("patient.view_all")) notFound();
  return <HandoffsDashboard />;
}
