"use client";

import { useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { createClient } from "@/lib/supabase/browser";

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const supabase = createClient();
    const { data, error: signInError } = await supabase.auth.signInWithPassword({ email, password });

    if (signInError || !data.user) {
      setError("Email or password is incorrect.");
      setPending(false);
      return;
    }

    const { data: staff } = await supabase
      .from("staff_users")
      .select("id, active")
      .eq("auth_user_id", data.user.id)
      .maybeSingle<{ id: string; active: boolean }>();

    if (!staff?.active) {
      await supabase.auth.signOut();
      router.replace("/account-disabled");
      router.refresh();
      return;
    }

    const requested = searchParams.get("next");
    router.replace(requested?.startsWith("/dashboard") ? requested : "/dashboard");
    router.refresh();
  }

  return (
    <form className="form-grid" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="email">Work email</label>
        <input id="email" name="email" type="email" autoComplete="email" required />
      </div>
      <div className="field">
        <div className="field-label-row"><label htmlFor="password">Password</label><Link href="/auth/forgot-password">Forgot password?</Link></div>
        <input id="password" name="password" type="password" autoComplete="current-password" required />
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <button className="primary-button" type="submit" disabled={pending}>
        {pending ? "Signing in…" : "Sign in securely"}
      </button>
      {searchParams.get("password_updated") ? <p className="notice" role="status">Password updated. Sign in with your new password.</p> : null}
    </form>
  );
}
