import { afterEach, describe, expect, it, vi } from "vitest";

import { getRun, listRuns, uploadPdf } from "@/lib/api";
import { ApiError } from "@/lib/errors";

const RUN = "11111111-1111-4111-8111-111111111111";
const RESEARCH = "22222222-2222-4222-8222-222222222222";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("loads the first page of runs", async () => {
    const fetchImpl = vi.fn(async () =>
      Response.json({
        items: [
          {
            run_id: RUN,
            research_id: RESEARCH,
            question: "How does the proxy stream results?",
            status: "completed",
            error_code: null,
            created_at: "2026-10-18T12:00:00Z",
            finished_at: "2026-10-18T12:01:00Z",
          },
        ],
        next_cursor: null,
      }),
    );
    const page = await listRuns(20, undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/research/runs?limit=20");
    expect(page.items[0]?.run_id).toBe(RUN);
    expect(page.items[0]).not.toHaveProperty("report");
  });

  it("loads run detail", async () => {
    const fetchImpl = vi.fn(async () =>
      Response.json({
        run: {
          run_id: RUN,
          research_id: RESEARCH,
          question: "How does the proxy stream results?",
          status: "partial",
          error_code: null,
          warnings: [],
          usage: null,
          created_at: "2026-10-18T12:00:00Z",
          started_at: "2026-10-18T12:00:01Z",
          finished_at: "2026-10-18T12:01:00Z",
          updated_at: "2026-10-18T12:01:00Z",
        },
        report: {
          title: "Proxy notes",
          summary: "The first frame arrives before the run finishes.",
          sections: [],
          limitations: [],
          status: "partial",
          sources: [],
        },
      }),
    );
    const detail = await getRun(RUN, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(`/api/v1/research/runs/${RUN}`);
    expect(detail.report?.status).toBe("partial");
  });

  it("uploads a PDF as research_id and file", async () => {
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const form = init?.body as FormData;
      expect(form.get("research_id")).toBe(RESEARCH);
      expect(form.get("file")).toBeInstanceOf(File);
      expect(init?.headers).toBeUndefined();
      return Response.json({
        source_id: RUN,
        research_id: RESEARCH,
        status: "indexed",
        chunk_count: 2,
        deduplicated: false,
      });
    });
    const indexed = await uploadPdf(RESEARCH, file, undefined, fetchImpl);
    expect(indexed.status).toBe("indexed");
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/evidence/pdf");
  });

  it("shows the envelope message and a fixed fallback otherwise", async () => {
    const envelope = vi.fn(async () =>
      Response.json(
        { error: { code: "invalid_request", message: "question: invalid value" } },
        { status: 422 },
      ),
    );
    await expect(getRun(RUN, envelope)).rejects.toMatchObject({
      code: "invalid_request",
      message: "question: invalid value",
    });

    const html = vi.fn(async () => new Response("<html>stack</html>", { status: 500 }));
    const error = await getRun(RUN, html).catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "The request could not be completed." });
    expect(String((error as ApiError).message)).not.toContain("stack");
  });
});
