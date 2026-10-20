import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";

export default function LoginPage() {
  return (
    <main className="auth-page">
      <section className="auth-intro" aria-label="Marina Smile operations">
        <div>
          <span className="auth-mark">MS</span>
          <p className="brand-title" style={{ fontSize: "1.1rem", fontWeight: 800 }}>Marina Smile</p>
        </div>
        <div className="auth-intro-copy">
          <p className="eyebrow" style={{ color: "#9fdad6" }}>Clinic operations</p>
          <h2 style={{ maxWidth: 560, fontSize: "clamp(2rem,4.4vw,4.2rem)", lineHeight: 1.04, margin: ".6rem 0 1rem" }}>
            A clearer day for every patient and clinician.
          </h2>
          <p style={{ maxWidth: 500, color: "#b9d3d1", lineHeight: 1.7 }}>
            Secure access to today&apos;s appointments, queues, payments, schedules, and handoffs.
          </p>
        </div>
        <small style={{ color: "#8eb7b4" }}>Authorized Marina Smile staff only</small>
      </section>
      <section className="auth-panel">
        <div className="auth-card">
          <p className="eyebrow">Staff portal</p>
          <h1>Welcome back</h1>
          <p className="muted">Sign in with the staff account issued by your clinic administrator.</p>
          <Suspense fallback={<p className="muted">Preparing secure sign-in…</p>}>
            <LoginForm />
          </Suspense>
        </div>
      </section>
    </main>
  );
}
