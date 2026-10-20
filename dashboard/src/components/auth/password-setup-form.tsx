"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { passwordValidationMessage } from "@/lib/password-policy";
import { createClient } from "@/lib/supabase/browser";

type PasswordSetupFormProps = {
  mode: "invite" | "recovery";
};

export function PasswordSetupForm({ mode }: PasswordSetupFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const hasLinkError = searchParams.has("error");
  const [checking, setChecking] = useState(true);
  const [validSession, setValidSession] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(
    hasLinkError ? "This secure link is invalid or has expired." : null,
  );
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    let active = true;
    void createClient().auth.getUser().then(({ data, error: sessionError }) => {
      if (!active) return;
      const valid = Boolean(data.user) && !sessionError && !hasLinkError;
      setValidSession(valid);
      if (!valid) setError("This secure link is invalid or has expired.");
      setChecking(false);
    });
    return () => { active = false; };
  }, [hasLinkError]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirm_password") ?? "");
    const validation = passwordValidationMessage(password);
    if (validation) { setError(validation); return; }
    if (password !== confirmation) { setError("Passwords do not match."); return; }

    setPending(true);
    const supabase = createClient();
    const { error: updateError } = await supabase.auth.updateUser({ password });
    if (updateError) {
      setError("Your password could not be updated. Request a new secure link and try again.");
      setPending(false);
      return;
    }
    await supabase.auth.signOut({ scope: "local" });
    setComplete(true);
    setPending(false);
    window.setTimeout(() => { router.replace("/login?password_updated=1"); router.refresh(); }, 1200);
  }

  if (checking) return <p className="muted">Checking your secure link…</p>;
  if (complete) return <p className="notice" role="status">Password saved. Redirecting you to sign in…</p>;

  return (
    <form className="form-grid" onSubmit={submit}>
      <div className="field">
        <label htmlFor={`${mode}-password`}>New password</label>
        <input id={`${mode}-password`} name="password" type="password" autoComplete="new-password" required disabled={!validSession || pending} />
        <small className="muted">At least 12 characters with uppercase, lowercase, and a number.</small>
      </div>
      <div className="field">
        <label htmlFor={`${mode}-confirm-password`}>Confirm password</label>
        <input id={`${mode}-confirm-password`} name="confirm_password" type="password" autoComplete="new-password" required disabled={!validSession || pending} />
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <button className="primary-button" type="submit" disabled={!validSession || pending}>
        {pending ? "Saving password…" : mode === "invite" ? "Set up account" : "Reset password"}
      </button>
    </form>
  );
}
