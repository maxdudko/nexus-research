const RUN_PATH = /^\/research\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const EVALUATION_PATH =
  /^\/admin\/evaluations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function isAllowlistedPath(value: string): boolean {
  return (
    value === "/" ||
    value === "/history" ||
    value === "/research/new" ||
    value === "/admin" ||
    value === "/admin/diagnostics" ||
    value === "/admin/usage" ||
    value === "/admin/evaluations" ||
    value === "/admin/evaluations/compare" ||
    RUN_PATH.test(value) ||
    EVALUATION_PATH.test(value)
  );
}

export function loginHref(pathname: string): string {
  if (!isAllowlistedPath(pathname)) return "/login";
  return `/login?next=${encodeURIComponent(pathname)}`;
}

export function destinationFromNext(value: string | null): string {
  if (value && isAllowlistedPath(value)) return value;
  return "/";
}
