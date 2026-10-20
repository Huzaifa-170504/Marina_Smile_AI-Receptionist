import { OverviewDashboard } from "@/components/overview-dashboard";
import { PaymentsDashboard } from "@/components/payments-dashboard";
import { QueueDashboard } from "@/components/queue-dashboard";
import { getStaffProfile } from "@/lib/auth";

export default async function DashboardPage() {
  const staff = await getStaffProfile();
  return (
    <div className="stack">
      <OverviewDashboard />
      {staff.role === "doctor" ? <QueueDashboard /> : null}
      {staff.role === "payment_verifier" ? <PaymentsDashboard /> : null}
    </div>
  );
}
