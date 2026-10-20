"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { createClient } from "@/lib/supabase/browser";

const GENERIC_MESSAGE = "If an account exists for this email, a password reset link has been sent.";

export function ForgotPasswordForm() {
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    try {
      await createClient().auth.resetPasswordForEmail(email, {
        redirectTo: window.location.origin,
      });
    } catch {
      // Deliberately return the same response for unknown accounts and provider failures.
    }
    setComplete(true);
    setPending(false);
  }

  if (complete) return <p className="notice" role="status">{GENERIC_MESSAGE}</p>;
  return (
    <form className="form-grid" onSubmit={submit}>
      <div className="field">
        <label htmlFor="recovery-email">Email address</label>
        <input id="recovery-email" name="email" type="email" autoComplete="email" required />
      </div>
      <button className="primary-button" type="submit" disabled={pending}>
        {pending ? "Sending…" : "Send reset link"}
      </button>
      <p className="muted" style={{ fontSize: ".82rem" }}>
        You must know your registered email. For security, this portal cannot discover or reveal staff email addresses.
      </p>
      <Link href="/login">Return to sign in</Link>
    </form>
  );
}
