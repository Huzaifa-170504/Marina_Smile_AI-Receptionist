import { DoctorsDashboard } from "@/components/doctors-dashboard";
import { requireRole } from "@/lib/auth";

export default async function DoctorsPage() {
  await requireRole("owner", "receptionist");
  return <DoctorsDashboard />;
}
