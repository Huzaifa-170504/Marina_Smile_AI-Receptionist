import type { EmailOtpType } from "@supabase/supabase-js";
import { type NextRequest, NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

const DESTINATIONS: Partial<Record<EmailOtpType, string>> = {
  invite: "/auth/setup-password",
  recovery: "/auth/reset-password",
};

export async function GET(request: NextRequest) {
  const tokenHash = request.nextUrl.searchParams.get("token_hash");
  const type = request.nextUrl.searchParams.get("type") as EmailOtpType | null;
  const destination = type ? DESTINATIONS[type] : undefined;
  const redirectUrl = new URL(destination ?? "/login", request.url);

  if (tokenHash && type && destination) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type });
    if (!error) return NextResponse.redirect(redirectUrl);
  }

  redirectUrl.pathname = destination ?? "/login";
  redirectUrl.search = "?error=invalid_or_expired";
  return NextResponse.redirect(redirectUrl);
}
