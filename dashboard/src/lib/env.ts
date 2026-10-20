export type PublicConfig = {
  supabaseUrl: string;
  supabaseKey: string;
};

let cachedConfig: PublicConfig | null = null;

export function getPublicConfig(): PublicConfig {
  if (cachedConfig) return cachedConfig;

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const supabaseKey =
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  if (!supabaseUrl || !supabaseKey) {
    throw new Error(
      "Missing public Supabase configuration. Check dashboard/.env.local.",
    );
  }

  cachedConfig = {
    supabaseUrl: supabaseUrl.replace(/\/$/, ""),
    supabaseKey,
  };
  return cachedConfig;
}

export function getApiBaseUrl(): string {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!apiBaseUrl) {
    throw new Error("Missing backend API configuration.");
  }
  return apiBaseUrl.replace(/\/$/, "");
}
