import { PaymentsDashboard } from "@/components/payments-dashboard";
import { requireRole } from "@/lib/auth";

export default async function PaymentsPage() {
  await requireRole("owner", "payment_verifier");
  return <PaymentsDashboard />;
}
