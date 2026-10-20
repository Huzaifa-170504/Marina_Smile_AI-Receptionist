import { DemoProxyConfigurationError, proxyDemoRequest } from "@/lib/demo-proxy";


export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    return await proxyDemoRequest(request, "/api/demo/status");
  } catch (error) {
    const message = error instanceof DemoProxyConfigurationError
      ? error.message
      : "The live demo is unavailable.";
    return Response.json({ detail: message }, { status: 503 });
  }
}
