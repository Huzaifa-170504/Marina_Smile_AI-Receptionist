"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { createClient } from "@/lib/supabase/browser";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    await createClient().auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <button className="secondary-button" type="button" onClick={logout} disabled={pending}>
      <LogOut size={15} aria-hidden="true" /> {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}
