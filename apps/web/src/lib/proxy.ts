const REQUEST_HEADERS = ["content-type", "accept"] as const;
const RESPONSE_HEADERS = ["content-type", "cache-control", "x-accel-buffering"] as const;

export function apiProxyTarget(): string {
  return process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function unavailable(): Response {
  return Response.json(
    {
      error: {
        code: "history_unavailable",
        message: "The research service is unavailable.",
      },
    },
    { status: 502 },
  );
}

export async function proxyApi(
  request: Request,
  path: string[],
  target = apiProxyTarget(),
): Promise<Response> {
  const incoming = new URL(request.url);
  const upstreamUrl = new URL(
    `/api/v1/${path.map((segment) => encodeURIComponent(segment)).join("/")}${incoming.search}`,
    target,
  );
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    signal: request.signal,
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }
  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    return unavailable();
  }
  const responseHeaders = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
