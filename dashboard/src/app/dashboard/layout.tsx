import type { ReactNode } from "react";

import { DashboardShell } from "@/components/dashboard-shell";
import { getStaffProfile } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const staff = await getStaffProfile();
  return <DashboardShell staff={staff}>{children}</DashboardShell>;
}
