import { QueueDashboard } from "@/components/queue-dashboard";
import { requireRole } from "@/lib/auth";

export default async function MyQueuePage() {
  await requireRole("doctor");
  return <QueueDashboard />;
}
