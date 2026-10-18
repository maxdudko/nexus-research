import { proxyApi } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function handle(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  return proxyApi(request, path);
}

export const GET = handle;
export const POST = handle;
