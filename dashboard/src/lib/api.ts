import { getPublicConfig } from "@/lib/env";
import { createClient } from "@/lib/supabase/browser";
import type { ApiErrorPayload } from "@/types/domain";

export class DashboardApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "DashboardApiError";
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const { data } = await createClient().auth.getSession();
  if (!data.session?.access_token) throw new DashboardApiError("Your session has expired.", 401);

  const response = await fetch(`${getPublicConfig().apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${data.session.access_token}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let payload: ApiErrorPayload = {};
    try { payload = (await response.json()) as ApiErrorPayload; } catch { /* safe fallback */ }
    throw new DashboardApiError(
      payload.detail || payload.message || "The operation could not be completed.",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
