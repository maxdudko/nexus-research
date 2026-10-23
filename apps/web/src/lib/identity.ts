export interface Identity {
  id: string;
  email: string;
  role: string;
}

export type IdentityResult =
  | { status: "ready"; identity: Identity }
  | { status: "anonymous" }
  | { status: "unavailable" };

export function readIdentity(value: unknown): Identity | null {
  if (typeof value !== "object" || value === null) return null;
  if (!("id" in value) || !("email" in value) || !("role" in value)) return null;
  const { id, email, role } = value;
  if (typeof id !== "string" || typeof email !== "string" || typeof role !== "string") return null;
  if (id.length === 0 || email.length === 0) return null;
  return { id, email, role };
}

export async function fetchIdentity(fetchImpl: typeof fetch = fetch): Promise<IdentityResult> {
  try {
    const response = await fetchImpl("/api/v1/auth/me", {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (response.status === 401) return { status: "anonymous" };
    if (!response.ok) return { status: "unavailable" };
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return { status: "unavailable" };
    }
    const identity = readIdentity(body);
    if (!identity) return { status: "unavailable" };
    return { status: "ready", identity };
  } catch {
    return { status: "unavailable" };
  }
}
