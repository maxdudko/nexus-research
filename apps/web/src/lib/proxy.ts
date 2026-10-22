const REQUEST_HEADERS = ["content-type", "accept", "origin", "x-csrf-token"] as const;
const RESPONSE_HEADERS = ["content-type", "cache-control", "x-accel-buffering"] as const;
const FORWARDED_COOKIES = new Set(["nexus_session", "nexus_csrf"]);

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

function forwardedCookie(header: string | null): string | null {
  if (!header) return null;
  const kept: string[] = [];
  for (const part of header.split(";")) {
    const trimmed = part.trim();
    const name = trimmed.split("=", 1)[0]?.trim();
    if (name && FORWARDED_COOKIES.has(name)) kept.push(trimmed);
  }
  return kept.length > 0 ? kept.join("; ") : null;
}

function allowedSetCookie(value: string): boolean {
  const name = value.split("=", 1)[0]?.trim();
  return name === "nexus_session" || name === "nexus_csrf";
}

function setCookieValues(headers: Headers): string[] {
  if (typeof headers.getSetCookie === "function") return headers.getSetCookie();
  return [];
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
  const cookie = forwardedCookie(request.headers.get("cookie"));
  if (cookie) headers.set("cookie", cookie);
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
  for (const value of setCookieValues(upstream.headers)) {
    if (allowedSetCookie(value)) responseHeaders.append("set-cookie", value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
