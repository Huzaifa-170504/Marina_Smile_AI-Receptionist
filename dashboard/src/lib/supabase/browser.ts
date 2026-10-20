import { createBrowserClient } from "@supabase/ssr";

import { getPublicConfig } from "@/lib/env";

export function createClient() {
  const { supabaseUrl, supabaseKey } = getPublicConfig();
  return createBrowserClient(supabaseUrl, supabaseKey);
}
