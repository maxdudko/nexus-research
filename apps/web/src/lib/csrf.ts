export const CSRF_COOKIE = "nexus_csrf";
export const CSRF_HEADER = "x-csrf-token";

export function readCookie(name: string, source?: string): string | null {
  const header = source ?? (typeof document === "undefined" ? "" : document.cookie);
  for (const part of header.split(";")) {
    const trimmed = part.trim();
    const separator = trimmed.indexOf("=");
    if (separator === -1) continue;
    if (trimmed.slice(0, separator) !== name) continue;
    return decodeURIComponent(trimmed.slice(separator + 1));
  }
  return null;
}

export function csrfHeaders(source?: string): Record<string, string> | undefined {
  const token = readCookie(CSRF_COOKIE, source);
  if (!token) return undefined;
  return { [CSRF_HEADER]: token };
}
