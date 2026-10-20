import { notFound } from "next/navigation";

import { ResourceTablePage } from "@/components/resource-table-page";
import { getStaffProfile } from "@/lib/auth";
import { RESOURCE_CONFIGS } from "@/lib/resource-config";

export default async function ResourcePage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  const config = RESOURCE_CONFIGS[section];
  if (!config) notFound();
  const staff = await getStaffProfile();
  if (!config.roles.includes(staff.role)) notFound();
  return <ResourceTablePage config={config} />;
}
