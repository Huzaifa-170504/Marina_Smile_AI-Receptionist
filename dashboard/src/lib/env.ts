export type PublicConfig = {
  supabaseUrl: string;
  supabaseKey: string;
  apiBaseUrl: string;
};

let cachedConfig: PublicConfig | null = null;

export function getPublicConfig(): PublicConfig {
  if (cachedConfig) return cachedConfig;

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const supabaseKey =
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!supabaseUrl || !supabaseKey || !apiBaseUrl) {
    throw new Error(
      "Missing public dashboard configuration. Check dashboard/.env.local.",
    );
  }

  cachedConfig = {
    supabaseUrl: supabaseUrl.replace(/\/$/, ""),
    supabaseKey,
    apiBaseUrl: apiBaseUrl.replace(/\/$/, ""),
  };
  return cachedConfig;
}
