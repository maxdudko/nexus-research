// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NewResearchPage from "@/app/(app)/research/new/page";

vi.mock("@/hooks/use-research-stream", () => ({
  useResearchStream: () => ({
    state: {
      researchId: "",
      question: "",
      phase: "idle",
      runId: null,
      stages: [],
      errorMessage: null,
      errorCode: null,
      fileName: null,
      fileSize: null,
      indexed: false,
      startedAt: null,
      lastFrameAt: null,
      lastHeartbeatAt: null,
      retryNotice: null,
    },
    setQuestion: vi.fn(),
    setFile: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    retry: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  createResearchScope: vi.fn(),
}));

import { createResearchScope } from "@/lib/api";

const createResearchScopeMock = vi.mocked(createResearchScope);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("new research page", () => {
  it("explains that leaving stops the request and does not mint a scope", () => {
    render(<NewResearchPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("New research");
    expect(screen.getByText(/does not continue in the background/)).toBeTruthy();
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });
});
