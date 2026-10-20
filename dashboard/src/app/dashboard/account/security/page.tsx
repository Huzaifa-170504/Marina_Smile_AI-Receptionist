import { AccountSecurityForm } from "@/components/auth/account-security-form";
import { PageHeader } from "@/components/page-header";

export default function AccountSecurityPage() {
  return <><PageHeader eyebrow="Your account" title="Security" description="Manage your own sign-in password without administrator intervention." />
    <section className="panel" style={{ maxWidth: 620 }}><div className="panel-header"><div><h2>Change password</h2><p className="muted">Your password is stored and verified only by Supabase Auth.</p></div></div><AccountSecurityForm /></section></>;
}
