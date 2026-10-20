import { ServicesManager } from "@/components/services-manager";
import { requireRole } from "@/lib/auth";

export default async function ServicesPage() {
  await requireRole("owner");
  return <ServicesManager />;
}
