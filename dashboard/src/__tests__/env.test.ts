import { afterEach, describe, expect, it, vi } from "vitest";


afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("dashboard environment boundaries", () => {
  it("allows Supabase authentication without a configured backend API", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://example.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "publishable-test-key");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    const { getApiBaseUrl, getPublicConfig } = await import("@/lib/env");

    expect(getPublicConfig()).toEqual({
      supabaseUrl: "https://example.supabase.co",
      supabaseKey: "publishable-test-key",
    });
    expect(() => getApiBaseUrl()).toThrow("Missing backend API configuration.");
  });
});
