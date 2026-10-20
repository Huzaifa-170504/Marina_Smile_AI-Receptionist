import { QueueDashboard } from "@/components/queue-dashboard";
import { requireRole } from "@/lib/auth";

export default async function QueuePage() {
  await requireRole("owner", "receptionist");
  return <QueueDashboard />;
}
