// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useResearchStream } from "@/hooks/use-research-stream";
import { ApiError } from "@/lib/errors";
import { PDF_LIMIT_BYTES } from "@/lib/research-session";
import type { RunDetailResponse, RunStatus } from "@/lib/types";

const RUN = "11111111-1111-4111-8111-111111111111";
const RESEARCH = "22222222-2222-4222-8222-222222222222";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => "/research/new",
}));

const scope = vi.hoisted(() => {
  const ids = [
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333331",
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
  ];
  let index = 0;
  async function mint(): Promise<string> {
    const id = ids[index] ?? ids[0];
    index += 1;
    return id;
  }
  const next = vi.fn(mint);
  return {
    reset: () => {
      index = 0;
      next.mockReset();
      next.mockImplementation(mint);
    },
    next,
  };
});

vi.mock("@/lib/api", () => ({
  uploadPdf: vi.fn(),
  openResearchStream: vi.fn(),
  getRun: vi.fn(),
  listRuns: vi.fn(),
  createResearchScope: scope.next,
}));

import { createResearchScope, getRun, openResearchStream, uploadPdf } from "@/lib/api";

const uploadPdfMock = vi.mocked(uploadPdf);
const openResearchStreamMock = vi.mocked(openResearchStream);
const getRunMock = vi.mocked(getRun);
const createResearchScopeMock = vi.mocked(createResearchScope);

function sseResponse(chunks: string[], hanging = false): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!hanging) controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function started(status = "running"): string {
  return `event: research.started\ndata: ${JSON.stringify({
    version: 1,
    run_id: RUN,
    research_id: RESEARCH,
    status,
  })}\n\n`;
}

function completed(status: "completed" | "partial" | "insufficient_evidence"): string {
  return `event: research.completed\ndata: ${JSON.stringify({
    version: 1,
    run_id: RUN,
    research_id: RESEARCH,
    status,
  })}\n\n`;
}

function detail(status: RunStatus, errorCode: string | null = null): RunDetailResponse {
  return {
    run: {
      run_id: RUN,
      research_id: RESEARCH,
      question: "How does the proxy stream results?",
      status,
      error_code: errorCode,
      warnings: [],
      usage: null,
      created_at: "2026-10-18T12:00:00Z",
      started_at: "2026-10-18T12:00:01Z",
      finished_at: status === "running" ? null : "2026-10-18T12:01:00Z",
      updated_at: "2026-10-18T12:01:00Z",
    },
    report: null,
  };
}

async function renderStream(navigate: (href: string) => void = vi.fn()) {
  let rendered: ReturnType<typeof renderHook<ReturnType<typeof useResearchStream>, unknown>> | undefined;
  await act(async () => {
    rendered = renderHook(() => useResearchStream(navigate));
    await Promise.resolve();
    await Promise.resolve();
  });
  if (!rendered) throw new Error("hook did not render");
  return rendered;
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("useResearchStream", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    scope.reset();
  });

  it("does not start a stream on mount", async () => {
    const { result } = await renderStream();
    expect(createResearchScopeMock).not.toHaveBeenCalled();
    expect(result.current.state.researchId).toBe("");
    expect(result.current.state.phase).toBe("idle");
    expect(openResearchStreamMock).not.toHaveBeenCalled();
    expect(uploadPdfMock).not.toHaveBeenCalled();

    act(() => {
      result.current.setQuestion("short");
    });
    act(() => {
      result.current.start();
    });
    expect(createResearchScopeMock).not.toHaveBeenCalled();
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).not.toHaveBeenCalled();
  });

  it("uploads the PDF and streams with the same research id", async () => {
    uploadPdfMock.mockResolvedValue({
      source_id: "33333333-3333-4333-8333-333333333333",
      research_id: "",
      status: "indexed",
      chunk_count: 1,
      deduplicated: false,
    });
    openResearchStreamMock.mockImplementation(async () => sseResponse([started(), completed("completed")]));
    getRunMock.mockResolvedValue({
      run: {
        run_id: RUN,
        research_id: RESEARCH,
        question: "How does the proxy stream results?",
        status: "completed",
        error_code: null,
        warnings: [],
        usage: null,
        created_at: "2026-10-18T12:00:00Z",
        started_at: "2026-10-18T12:00:01Z",
        finished_at: "2026-10-18T12:01:00Z",
        updated_at: "2026-10-18T12:01:00Z",
      },
      report: null,
    });
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(file);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
    expect(createResearchScopeMock.mock.invocationCallOrder[0]).toBeLessThan(
      uploadPdfMock.mock.invocationCallOrder[0],
    );
    expect(uploadPdfMock).toHaveBeenCalledWith(RESEARCH, file, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenCalledWith(
      { question: "How does the proxy stream results?", research_id: RESEARCH },
      expect.any(AbortSignal),
    );
    expect(getRunMock).toHaveBeenCalledWith(RUN);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(`/research/${RUN}`);
    expect(result.current.state.phase).toBe("completed");
  });

  it("stays on the error when run detail fails", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started(), completed("partial")]));
    getRunMock.mockRejectedValue(new ApiError("history_unavailable", "History is unavailable.", 503));
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("failed");
    expect(result.current.state.errorMessage).toBe("History is unavailable.");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("treats stop as interrupted and a short stream as disconnected", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()], true));
    getRunMock.mockResolvedValue(detail("interrupted", "interrupted"));
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    act(() => {
      result.current.start();
    });
    await flush();
    expect(result.current.state.phase).toBe("streaming");
    act(() => {
      result.current.stop();
    });
    await flush();
    expect(result.current.state.phase).toBe("interrupted");
    expect(getRunMock).toHaveBeenCalledWith(RUN);
    expect(navigate).not.toHaveBeenCalled();

    openResearchStreamMock.mockResolvedValue(
      sseResponse([
        `event: research.progress\ndata: ${JSON.stringify({
          version: 1,
          run_id: RUN,
          stage: "planning",
          iteration: 0,
          message: "Planning the research",
          counts: { pages_indexed: 0, search_calls: 0 },
        })}\n\n`,
      ]),
    );
    act(() => {
      result.current.retry();
    });
    expect(result.current.state.researchId).toBe("");
    expect(result.current.state.retryNotice).toContain("new run");
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
    act(() => {
      result.current.start();
    });
    await flush();
    expect(result.current.state.phase).toBe("disconnected");
    expect(result.current.state.researchId).toBe("33333333-3333-4333-8333-333333333331");
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      {
        question: "How does the proxy stream results?",
        research_id: "33333333-3333-4333-8333-333333333331",
      },
      expect.any(AbortSignal),
    );
    expect(getRunMock).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("reads a dropped run once and keeps a stored running row disconnected", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()]));
    getRunMock.mockResolvedValue(detail("running"));
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(getRunMock).toHaveBeenCalledTimes(1);
    expect(getRunMock).toHaveBeenCalledWith(RUN);
    expect(result.current.state.phase).toBe("disconnected");
    expect(result.current.state.errorMessage).toBe(
      "Last known state: running; execution is not confirmed.",
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
    await flush();
    expect(getRunMock).toHaveBeenCalledTimes(1);
  });

  it("opens a stored report when the stream ends without a terminal frame", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()]));
    getRunMock.mockResolvedValue(detail("partial"));
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("partial");
    expect(navigate).toHaveBeenCalledWith(`/research/${RUN}`);
    expect(getRunMock).toHaveBeenCalledTimes(1);
  });

  it("shows a stored failure after one read when the stream errors", async () => {
    const encoder = new TextEncoder();
    let reads = 0;
    openResearchStreamMock.mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          pull(controller) {
            reads += 1;
            if (reads === 1) {
              controller.enqueue(encoder.encode(started()));
              return;
            }
            controller.error(new Error("socket closed"));
          },
        }),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      ),
    );
    getRunMock.mockResolvedValue(detail("failed", "provider_unavailable"));
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("failed");
    expect(result.current.state.errorMessage).toBe("Research failed (provider_unavailable).");
    expect(getRunMock).toHaveBeenCalledTimes(1);
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
  });

  it("shows a stored interruption after the stream drops", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()]));
    getRunMock.mockResolvedValue(detail("interrupted", "interrupted"));
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("interrupted");
    expect(result.current.state.errorMessage).toBe("Research was interrupted (interrupted).");
    expect(getRunMock).toHaveBeenCalledTimes(1);
  });

  it("stays disconnected when the follow-up read fails", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()]));
    getRunMock.mockRejectedValue(new ApiError("history_unavailable", "History is unavailable.", 503));
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("disconnected");
    expect(result.current.state.errorMessage).toBe(
      "Last known state: running; execution is not confirmed.",
    );
    expect(getRunMock).toHaveBeenCalledTimes(1);
  });

  it("reindexes a kept PDF on retry, then a replacement, and skips a cleared file", async () => {
    uploadPdfMock.mockImplementation(async (researchId: string) => ({
      source_id: "33333333-3333-4333-8333-333333333333",
      research_id: researchId,
      status: "indexed",
      chunk_count: 1,
      deduplicated: false,
    }));
    openResearchStreamMock.mockImplementation(async () =>
      sseResponse([started(), completed("completed")]),
    );
    getRunMock.mockResolvedValue({
      run: {
        run_id: RUN,
        research_id: RESEARCH,
        question: "How does the proxy stream results?",
        status: "completed",
        error_code: null,
        warnings: [],
        usage: null,
        created_at: "2026-10-18T12:00:00Z",
        started_at: "2026-10-18T12:00:01Z",
        finished_at: "2026-10-18T12:01:00Z",
        updated_at: "2026-10-18T12:01:00Z",
      },
      report: null,
    });
    const { result } = await renderStream();
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(file);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const firstId = RESEARCH;
    expect(uploadPdfMock).toHaveBeenCalledWith(firstId, file, expect.any(AbortSignal));

    const scopesBeforeRetry = createResearchScopeMock.mock.calls.length;
    act(() => {
      result.current.retry();
    });
    expect(result.current.state.researchId).toBe("");
    expect(result.current.state.question).toBe("How does the proxy stream results?");
    expect(result.current.state.indexed).toBe(false);
    expect(result.current.state.fileName).toBe("notes.pdf");
    expect(createResearchScopeMock).toHaveBeenCalledTimes(scopesBeforeRetry);
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const secondId = "33333333-3333-4333-8333-333333333331";
    expect(createResearchScopeMock.mock.calls.length).toBe(scopesBeforeRetry + 1);
    expect(uploadPdfMock).toHaveBeenCalledWith(secondId, file, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: secondId },
      expect.any(AbortSignal),
    );

    const replacement = new File(["%PDF-1.4"], "replacement.pdf", { type: "application/pdf" });
    act(() => {
      result.current.retry();
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(scopesBeforeRetry + 1);
    act(() => {
      result.current.setFile(replacement);
    });
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const thirdId = "44444444-4444-4444-8444-444444444444";
    const replacementCall = uploadPdfMock.mock.calls.at(-1);
    expect(replacementCall?.[0]).toBe(thirdId);
    expect(replacementCall?.[1]).toBe(replacement);
    expect(replacementCall?.[1]).not.toBe(file);

    act(() => {
      result.current.retry();
    });
    act(() => {
      result.current.setFile(null);
    });
    expect(result.current.state.fileName).toBeNull();
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const fourthId = "55555555-5555-4555-8555-555555555555";
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: fourthId },
      expect.any(AbortSignal),
    );
  });

  it("does not open a stream when indexing fails, and retries that scope", async () => {
    uploadPdfMock.mockRejectedValueOnce(
      new ApiError("empty_document", "The document has no extractable text.", 422),
    );
    uploadPdfMock.mockResolvedValueOnce({
      source_id: "33333333-3333-4333-8333-333333333333",
      research_id: RESEARCH,
      status: "indexed",
      chunk_count: 1,
      deduplicated: false,
    });
    openResearchStreamMock.mockResolvedValue(sseResponse([started(), completed("completed")]));
    getRunMock.mockResolvedValue(detail("completed"));
    const { result } = await renderStream();
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(file);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("upload_failed");
    expect(result.current.state.researchId).toBe(RESEARCH);
    expect(openResearchStreamMock).not.toHaveBeenCalled();

    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
    expect(uploadPdfMock).toHaveBeenLastCalledWith(RESEARCH, file, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
  });

  it("mints a new scope when the file changes after an upload failure", async () => {
    uploadPdfMock.mockRejectedValueOnce(
      new ApiError("empty_document", "The document has no extractable text.", 422),
    );
    uploadPdfMock.mockResolvedValueOnce({
      source_id: "33333333-3333-4333-8333-333333333333",
      research_id: "33333333-3333-4333-8333-333333333331",
      status: "indexed",
      chunk_count: 1,
      deduplicated: false,
    });
    openResearchStreamMock.mockResolvedValue(sseResponse([started(), completed("completed")]));
    getRunMock.mockResolvedValue(detail("completed"));
    const { result } = await renderStream();
    const first = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    const replacement = new File(["%PDF-1.4"], "replacement.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(first);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.researchId).toBe(RESEARCH);
    act(() => {
      result.current.setFile(replacement);
    });
    expect(result.current.state.researchId).toBe("");
    expect(result.current.state.phase).toBe("idle");
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const nextId = "33333333-3333-4333-8333-333333333331";
    expect(createResearchScopeMock).toHaveBeenCalledTimes(2);
    expect(uploadPdfMock).toHaveBeenLastCalledWith(nextId, replacement, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: nextId },
      expect.any(AbortSignal),
    );
  });

  it("mints a new scope when a failed upload is cleared before the next start", async () => {
    uploadPdfMock.mockRejectedValueOnce(
      new ApiError("empty_document", "The document has no extractable text.", 422),
    );
    openResearchStreamMock.mockResolvedValue(sseResponse([started(), completed("completed")]));
    getRunMock.mockResolvedValue(detail("completed"));
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" }));
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    act(() => {
      result.current.setFile(null);
    });
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(2);
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      {
        question: "How does the proxy stream results?",
        research_id: "33333333-3333-4333-8333-333333333331",
      },
      expect.any(AbortSignal),
    );
  });

  it("does not reuse a failed upload scope after an oversized replacement", async () => {
    uploadPdfMock.mockRejectedValueOnce(
      new ApiError("empty_document", "The document has no extractable text.", 422),
    );
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" }));
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.researchId).toBe(RESEARCH);
    act(() => {
      result.current.setFile(
        new File([new Uint8Array(PDF_LIMIT_BYTES + 1)], "big.pdf", { type: "application/pdf" }),
      );
    });
    expect(result.current.state.phase).toBe("upload_failed");
    expect(result.current.state.researchId).toBe("");
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
    expect(uploadPdfMock).toHaveBeenCalledTimes(1);
    expect(openResearchStreamMock).not.toHaveBeenCalled();
  });

  it("shows Retry-After for a scope limit and does not upload or stream", async () => {
    createResearchScopeMock.mockRejectedValue(
      new ApiError("too_many_requests", "Too many research runs are in progress.", 429, 30),
    );
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("scope_failed");
    expect(result.current.state.errorMessage).toBe(
      "Too many research runs are in progress. Try again in 30 seconds.",
    );
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).not.toHaveBeenCalled();
    await flush();
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
  });

  it("shows a stream limit once and does not open another stream", async () => {
    openResearchStreamMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "too_many_requests",
            message: "Too many research runs are in progress.",
          },
        }),
        {
          status: 429,
          headers: { "content-type": "application/json", "Retry-After": "12" },
        },
      ),
    );
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current.state.phase).toBe("failed");
    expect(result.current.state.errorMessage).toContain("Try again in 12 seconds.");
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
    await flush();
    expect(openResearchStreamMock).toHaveBeenCalledTimes(1);
  });

  it("mints once when start is called twice in one turn", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([], true));
    let resolveScope: (id: string) => void = () => undefined;
    createResearchScopeMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveScope = resolve;
        }),
    );
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    act(() => {
      result.current.start();
      result.current.start();
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveScope(RESEARCH);
      await Promise.resolve();
    });
    expect(createResearchScopeMock).toHaveBeenCalledTimes(1);
  });

  it("does not upload or stream when unmounted before the scope resolves", async () => {
    let resolveScope: (id: string) => void = () => undefined;
    createResearchScopeMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveScope = resolve;
        }),
    );
    const { result, unmount } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    act(() => {
      result.current.start();
    });
    unmount();
    await act(async () => {
      resolveScope(RESEARCH);
      await Promise.resolve();
    });
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).not.toHaveBeenCalled();
  });

  it("replaces to the allowlisted login path on 401", async () => {
    createResearchScopeMock.mockRejectedValue(
      new ApiError("unauthenticated", "Authentication is required.", 401),
    );
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Fresearch%2Fnew");
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).not.toHaveBeenCalled();
  });

  it("does not navigate when stop interrupts an upload", async () => {
    uploadPdfMock.mockImplementation(
      (_researchId: string, _file: File, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        }),
    );
    const navigate = vi.fn();
    const { result } = await renderStream(navigate);
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" }));
    });
    act(() => {
      result.current.start();
    });
    await flush();
    expect(result.current.state.phase).toBe("uploading");
    act(() => {
      result.current.stop();
    });
    await flush();
    expect(result.current.state.phase).toBe("interrupted");
    expect(openResearchStreamMock).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect(getRunMock).not.toHaveBeenCalled();
  });

  it("shows stalled when no frame arrives for 45 seconds", async () => {
    vi.useFakeTimers();
    openResearchStreamMock.mockResolvedValue(sseResponse([started()], true));
    const { result } = await renderStream();
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
    });
    await act(async () => {
      result.current.start();
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.state.phase).toBe("streaming");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(result.current.state.phase).toBe("stalled");
    expect(result.current.state.stages).toEqual([]);
  });
});
