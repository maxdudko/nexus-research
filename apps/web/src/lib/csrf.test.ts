import { describe, expect, it } from "vitest";

import { csrfHeaders, readCookie } from "@/lib/csrf";

describe("csrf header", () => {
  it("reads only the csrf cookie", () => {
    const source = "other=no; nexus_csrf=abc%2B; nexus_session=secret";
    expect(readCookie("nexus_csrf", source)).toBe("abc+");
    expect(readCookie("missing", source)).toBeNull();
    expect(csrfHeaders(source)).toEqual({ "x-csrf-token": "abc+" });
    expect(csrfHeaders("nexus_session=secret")).toBeUndefined();
  });
});
