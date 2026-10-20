import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export default function ForgotPasswordPage() {
  return <main className="auth-page single-auth"><section className="auth-panel"><div className="auth-card">
    <p className="eyebrow">Account recovery</p><h1>Forgot password?</h1>
    <p className="muted">Enter your registered work email to request a secure reset link.</p>
    <ForgotPasswordForm />
  </div></section></main>;
}
