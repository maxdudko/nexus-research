import { describe, expect, it } from "vitest";

import { ApiError, errorFromResponse } from "@/lib/errors";

function limited(retryAfter: string): Response {
  return new Response(
    JSON.stringify({
      error: {
        code: "too_many_requests",
        message: "Too many research runs are in progress.",
      },
    }),
    {
      status: 429,
      headers: { "content-type": "application/json", "Retry-After": retryAfter },
    },
  );
}

describe("errorFromResponse", () => {
  it("appends an integer Retry-After and ignores an HTTP date", async () => {
    const seconds = await errorFromResponse(limited("30"));
    expect(seconds).toBeInstanceOf(ApiError);
    expect(seconds.retryAfter).toBe(30);
    expect(seconds.message).toBe(
      "Too many research runs are in progress. Try again in 30 seconds.",
    );

    const dated = await errorFromResponse(limited("Wed, 21 Oct 2026 07:28:00 GMT"));
    expect(dated.retryAfter).toBeNull();
    expect(dated.message).toBe("Too many research runs are in progress.");
  });
});
