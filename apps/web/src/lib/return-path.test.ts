import { describe, expect, it } from "vitest";

import { destinationFromNext, isAllowlistedPath, loginHref } from "@/lib/return-path";

const RUN = "/research/11111111-1111-4111-8111-111111111111";

describe("return path", () => {
  it("allows the dashboard, history, new research, and one run id", () => {
    expect(isAllowlistedPath("/")).toBe(true);
    expect(isAllowlistedPath("/history")).toBe(true);
    expect(isAllowlistedPath("/research/new")).toBe(true);
    expect(isAllowlistedPath(RUN)).toBe(true);
    expect(isAllowlistedPath("/admin")).toBe(true);
    expect(isAllowlistedPath("/admin/diagnostics")).toBe(true);
    expect(isAllowlistedPath("/admin/usage")).toBe(true);
    expect(isAllowlistedPath("/admin/evaluations")).toBe(true);
    expect(isAllowlistedPath("/admin/evaluations/compare")).toBe(true);
    expect(isAllowlistedPath("/admin/evaluations/11111111-1111-4111-8111-111111111111")).toBe(true);
  });

  it("rejects off-site and unknown paths", () => {
    expect(isAllowlistedPath("//evil.example")).toBe(false);
    expect(isAllowlistedPath("https://evil.example")).toBe(false);
    expect(isAllowlistedPath("/history/extra")).toBe(false);
    expect(isAllowlistedPath("/research/not-a-uuid")).toBe(false);
    expect(isAllowlistedPath("/admin/extra")).toBe(false);
    expect(isAllowlistedPath("/admin/diagnostics/extra")).toBe(false);
    expect(isAllowlistedPath("/admin/usage/extra")).toBe(false);
    expect(isAllowlistedPath("/admin/evaluations/compare/extra")).toBe(false);
    expect(isAllowlistedPath("/admin/evaluations/not-a-uuid")).toBe(false);
    expect(destinationFromNext("https://evil.example")).toBe("/");
    expect(destinationFromNext(null)).toBe("/");
  });

  it("puts an allowlisted path on the login redirect", () => {
    expect(loginHref("/history")).toBe("/login?next=%2Fhistory");
    expect(loginHref(RUN)).toBe(`/login?next=${encodeURIComponent(RUN)}`);
    expect(loginHref("/settings")).toBe("/login");
    expect(loginHref("/admin/evaluations/compare")).toBe("/login?next=%2Fadmin%2Fevaluations%2Fcompare");
    expect(loginHref("/admin")).toBe("/login?next=%2Fadmin");
    expect(destinationFromNext("/research/new")).toBe("/research/new");
  });
});
