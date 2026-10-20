import { createHmac } from "node:crypto";


const REQUEST_TIMEOUT_MS = 20_000;

type DemoProxyConfig = {
  backendUrl: string;
  token: string;
  fingerprintSecret: string;
};

export class DemoProxyConfigurationError extends Error {}

function getDemoProxyConfig(): DemoProxyConfig {
  const backendUrl = process.env.DEMO_BACKEND_URL?.trim();
  const token = process.env.DEMO_API_TOKEN?.trim();
  const fingerprintSecret = process.env.DEMO_FINGERPRINT_SECRET?.trim();
  if (!backendUrl || !token || !fingerprintSecret) {
    throw new DemoProxyConfigurationError("The live demo is not configured.");
  }
  return {
    backendUrl: backendUrl.replace(/\/$/, ""),
    token,
    fingerprintSecret,
  };
}

function readVisitorAddress(request: Request): string {
  const forwarded =
    request.headers.get("x-vercel-forwarded-for") ??
    request.headers.get("x-forwarded-for") ??
    "local-preview";
  return forwarded.split(",", 1)[0]?.trim() || "local-preview";
}

export async function proxyDemoRequest(
  request: Request,
  path: "/api/demo/status" | "/api/demo/query",
  body?: string,
): Promise<Response> {
  const config = getDemoProxyConfig();
  const visitorHash = createHmac("sha256", config.fingerprintSecret)
    .update(readVisitorAddress(request))
    .digest("hex");
  try {
    const response = await fetch(`${config.backendUrl}${path}`, {
      method: body === undefined ? "GET" : "POST",
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Demo-Token": config.token,
        "X-Demo-Visitor": visitorHash,
      },
      body,
    });
    const payload = await response.text();
    return new Response(payload, {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json(
      { detail: "The live backend is temporarily unavailable." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
