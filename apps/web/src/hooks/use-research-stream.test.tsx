// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useResearchStream } from "@/hooks/use-research-stream";
import { ApiError } from "@/lib/errors";
import type { RunDetailResponse, RunStatus } from "@/lib/types";

const RUN = "11111111-1111-4111-8111-111111111111";
const RESEARCH = "22222222-2222-4222-8222-222222222222";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  uploadPdf: vi.fn(),
  openResearchStream: vi.fn(),
  getRun: vi.fn(),
  listRuns: vi.fn(),
}));

import { getRun, openResearchStream, uploadPdf } from "@/lib/api";

const uploadPdfMock = vi.mocked(uploadPdf);
const openResearchStreamMock = vi.mocked(openResearchStream);
const getRunMock = vi.mocked(getRun);

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

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("useResearchStream", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("does not start a stream on mount", () => {
    renderHook(() => useResearchStream(vi.fn()));
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
    const { result } = renderHook(() => useResearchStream(navigate));
    const researchId = result.current.state.researchId;
    uploadPdfMock.mockResolvedValue({
      source_id: "33333333-3333-4333-8333-333333333333",
      research_id: researchId,
      status: "indexed",
      chunk_count: 1,
      deduplicated: false,
    });
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(file);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(uploadPdfMock).toHaveBeenCalledWith(researchId, file, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenCalledWith(
      { question: "How does the proxy stream results?", research_id: researchId },
      expect.any(AbortSignal),
    );
    expect(getRunMock).toHaveBeenCalledWith(RUN);
    expect(navigate).toHaveBeenCalledWith(`/research/${RUN}`);
    expect(result.current.state.phase).toBe("completed");
  });

  it("stays on the error when run detail fails", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started(), completed("partial")]));
    getRunMock.mockRejectedValue(new ApiError("history_unavailable", "History is unavailable.", 503));
    const navigate = vi.fn();
    const { result } = renderHook(() => useResearchStream(navigate));
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
    const { result } = renderHook(() => useResearchStream(vi.fn()));
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
    const nextId = result.current.state.researchId;
    expect(result.current.state.retryNotice).toContain("new run");
    act(() => {
      result.current.start();
    });
    await flush();
    expect(result.current.state.phase).toBe("disconnected");
    expect(result.current.state.researchId).toBe(nextId);
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: nextId },
      expect.any(AbortSignal),
    );
    expect(getRunMock).not.toHaveBeenCalled();
  });

  it("reads a dropped run once and keeps a stored running row disconnected", async () => {
    openResearchStreamMock.mockResolvedValue(sseResponse([started()]));
    getRunMock.mockResolvedValue(detail("running"));
    const navigate = vi.fn();
    const { result } = renderHook(() => useResearchStream(navigate));
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
    const { result } = renderHook(() => useResearchStream(navigate));
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
    const { result } = renderHook(() => useResearchStream(vi.fn()));
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
    const { result } = renderHook(() => useResearchStream(vi.fn()));
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
    const { result } = renderHook(() => useResearchStream(vi.fn()));
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
    const { result } = renderHook(() => useResearchStream(vi.fn()));
    const firstId = result.current.state.researchId;
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    act(() => {
      result.current.setQuestion("How does the proxy stream results?");
      result.current.setFile(file);
    });
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(uploadPdfMock).toHaveBeenCalledWith(firstId, file, expect.any(AbortSignal));

    act(() => {
      result.current.retry();
    });
    const secondId = result.current.state.researchId;
    expect(secondId).not.toBe(firstId);
    expect(result.current.state.indexed).toBe(false);
    expect(result.current.state.fileName).toBe("notes.pdf");
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(uploadPdfMock).toHaveBeenCalledWith(secondId, file, expect.any(AbortSignal));
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: secondId },
      expect.any(AbortSignal),
    );

    const replacement = new File(["%PDF-1.4"], "replacement.pdf", { type: "application/pdf" });
    act(() => {
      result.current.retry();
      result.current.setFile(replacement);
    });
    const thirdId = result.current.state.researchId;
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const replacementCall = uploadPdfMock.mock.calls.at(-1);
    expect(replacementCall?.[0]).toBe(thirdId);
    expect(replacementCall?.[1]).toBe(replacement);
    expect(replacementCall?.[1]).not.toBe(file);

    act(() => {
      result.current.retry();
      result.current.setFile(null);
    });
    const fourthId = result.current.state.researchId;
    expect(result.current.state.fileName).toBeNull();
    uploadPdfMock.mockClear();
    await act(async () => {
      result.current.start();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(uploadPdfMock).not.toHaveBeenCalled();
    expect(openResearchStreamMock).toHaveBeenLastCalledWith(
      { question: "How does the proxy stream results?", research_id: fourthId },
      expect.any(AbortSignal),
    );
  });

  it("shows stalled when no frame arrives for 45 seconds", async () => {
    vi.useFakeTimers();
    openResearchStreamMock.mockResolvedValue(sseResponse([started()], true));
    const { result } = renderHook(() => useResearchStream(vi.fn()));
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
