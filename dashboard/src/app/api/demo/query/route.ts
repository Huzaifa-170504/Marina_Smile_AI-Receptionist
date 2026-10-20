import { DemoProxyConfigurationError, proxyDemoRequest } from "@/lib/demo-proxy";


export const runtime = "nodejs";

const MAX_REQUEST_BYTES = 2_048;

export async function POST(request: Request) {
  const contentLength = Number(request.headers.get("content-length") ?? 0);
  if (contentLength > MAX_REQUEST_BYTES) {
    return Response.json({ detail: "Demo request is too large." }, { status: 413 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "A valid JSON request is required." }, { status: 400 });
  }

  if (!payload || typeof payload !== "object") {
    return Response.json({ detail: "A valid demo request is required." }, { status: 422 });
  }
  const candidate = payload as Record<string, unknown>;
  const mode = candidate.mode;
  const query = typeof candidate.query === "string" ? candidate.query.trim() : "";
  if ((mode !== "knowledge" && mode !== "availability") || query.length < 3 || query.length > 300) {
    return Response.json({ detail: "Choose a demo mode and enter a query of 3 to 300 characters." }, { status: 422 });
  }

  try {
    return await proxyDemoRequest(
      request,
      "/api/demo/query",
      JSON.stringify({ mode, query }),
    );
  } catch (error) {
    const message = error instanceof DemoProxyConfigurationError
      ? error.message
      : "The live demo is unavailable.";
    return Response.json({ detail: message }, { status: 503 });
  }
}
