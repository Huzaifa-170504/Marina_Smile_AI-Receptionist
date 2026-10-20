"use client";

import { useState, type FormEvent } from "react";

import { passwordValidationMessage } from "@/lib/password-policy";
import { createClient } from "@/lib/supabase/browser";

export function AccountSecurityForm() {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError(null); setMessage(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const currentPassword = String(form.get("current_password") ?? "");
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirm_password") ?? "");
    const validation = passwordValidationMessage(password);
    if (validation) { setError(validation); setPending(false); return; }
    if (password !== confirmation) { setError("Passwords do not match."); setPending(false); return; }
    const { error: updateError } = await createClient().auth.updateUser({
      password,
      current_password: currentPassword,
    });
    if (updateError) setError("Password change failed. Check your current password and try again.");
    else { setMessage("Your password has been changed."); formElement.reset(); }
    setPending(false);
  }

  return <form className="form-grid" onSubmit={submit}>
    <div className="field"><label htmlFor="current-password">Current password</label><input id="current-password" name="current_password" type="password" autoComplete="current-password" required /></div>
    <div className="field"><label htmlFor="new-password">New password</label><input id="new-password" name="password" type="password" autoComplete="new-password" required /><small className="muted">At least 12 characters with uppercase, lowercase, and a number.</small></div>
    <div className="field"><label htmlFor="confirm-new-password">Confirm new password</label><input id="confirm-new-password" name="confirm_password" type="password" autoComplete="new-password" required /></div>
    {error ? <p className="form-error" role="alert">{error}</p> : null}{message ? <p className="notice" role="status">{message}</p> : null}
    <button className="primary-button" type="submit" disabled={pending}>{pending ? "Changing password…" : "Change password"}</button>
  </form>;
}
