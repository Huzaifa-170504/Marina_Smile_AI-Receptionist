import { ScheduleManager } from "@/components/schedule-manager";
import { requireRole } from "@/lib/auth";

export default async function MyAvailabilityPage() {
  await requireRole("doctor");
  return <ScheduleManager />;
}
