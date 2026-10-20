import { LogoutButton } from "@/components/auth/logout-button";

export default function AccountDisabledPage() {
  return (
    <main className="auth-page">
      <section className="auth-intro">
        <span className="auth-mark">MS</span>
        <div className="auth-intro-copy"><h2>Account access is unavailable.</h2></div>
        <small>Marina Smile staff portal</small>
      </section>
      <section className="auth-panel">
        <div className="auth-card">
          <p className="eyebrow">Access denied</p>
          <h1>Contact the clinic owner</h1>
          <p className="muted">This account is not linked to an active Marina Smile staff profile.</p>
          <div style={{ marginTop: "1.5rem" }}><LogoutButton /></div>
        </div>
      </section>
    </main>
  );
}
