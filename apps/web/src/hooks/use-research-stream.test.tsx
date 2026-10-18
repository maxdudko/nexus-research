// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useResearchStream } from "@/hooks/use-research-stream";
import { ApiError } from "@/lib/errors";

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
