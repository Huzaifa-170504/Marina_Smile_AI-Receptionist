import { ScheduleManager } from "@/components/schedule-manager";
import { requireRole } from "@/lib/auth";

export default async function SchedulePage() {
  await requireRole("owner", "receptionist");
  return <ScheduleManager />;
}
