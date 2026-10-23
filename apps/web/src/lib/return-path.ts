const RUN_PATH = /^\/research\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function isAllowlistedPath(value: string): boolean {
  return value === "/" || value === "/history" || value === "/research/new" || RUN_PATH.test(value);
}

export function loginHref(pathname: string): string {
  if (!isAllowlistedPath(pathname)) return "/login";
  return `/login?next=${encodeURIComponent(pathname)}`;
}

export function destinationFromNext(value: string | null): string {
  if (value && isAllowlistedPath(value)) return value;
  return "/";
}
