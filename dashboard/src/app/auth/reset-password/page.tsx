import { Suspense } from "react";

import { PasswordSetupForm } from "@/components/auth/password-setup-form";

export default function ResetPasswordPage() {
  return <main className="auth-page single-auth"><section className="auth-panel"><div className="auth-card">
    <p className="eyebrow">Account recovery</p><h1>Reset your password</h1>
    <p className="muted">Choose a new password for your staff account.</p>
    <Suspense fallback={<p className="muted">Checking your secure link…</p>}><PasswordSetupForm mode="recovery" /></Suspense>
  </div></section></main>;
}
