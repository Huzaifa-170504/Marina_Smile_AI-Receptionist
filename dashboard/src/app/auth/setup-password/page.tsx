import { Suspense } from "react";

import { PasswordSetupForm } from "@/components/auth/password-setup-form";

export default function SetupPasswordPage() {
  return <main className="auth-page single-auth"><section className="auth-panel"><div className="auth-card">
    <p className="eyebrow">Staff account</p><h1>Set up your account</h1>
    <p className="muted">Choose a secure password for your Marina Smile staff account.</p>
    <Suspense fallback={<p className="muted">Preparing secure setup…</p>}><PasswordSetupForm mode="invite" /></Suspense>
  </div></section></main>;
}
