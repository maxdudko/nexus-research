// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ResearchRunPage from "@/app/(app)/research/[runId]/page";
import { ApiError } from "@/lib/errors";
import type { RunDetailResponse } from "@/lib/types";

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ runId: "11111111-1111-4111-8111-111111111111" }),
  useRouter: () => navigation,
}));

vi.mock("@/lib/api", () => ({
  getRun: vi.fn(),
}));

import { getRun } from "@/lib/api";

const getRunMock = vi.mocked(getRun);
const RUN = "11111111-1111-4111-8111-111111111111";

function detail(overrides: Partial<RunDetailResponse["run"]> = {}, report: RunDetailResponse["report"] = null): RunDetailResponse {
  return {
    run: {
      run_id: RUN,
      research_id: "33333333-3333-4333-8333-333333333333",
      question: "What was still running?",
      status: "running",
      error_code: null,
      warnings: [],
      usage: null,
      created_at: "2026-10-18T12:00:00Z",
      started_at: "2026-10-18T12:00:01Z",
      finished_at: null,
      updated_at: "2026-10-18T12:05:00Z",
      ...overrides,
    },
    report,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ResearchRunPage", () => {
  it("shows a stale running row with its timestamp", async () => {
    getRunMock.mockResolvedValue(detail());
    render(<ResearchRunPage />);
    const note = await screen.findByText(/Last known state: running; execution is not confirmed/);
    expect(note.textContent).toContain(new Date("2026-10-18T12:05:00Z").toLocaleString());
    expect(screen.getByText("No report is stored for this run.")).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toContain("currently researching");
  });

  it("shows an interrupted run without a report body", async () => {
    getRunMock.mockResolvedValue(
      detail({
        status: "interrupted",
        error_code: "interrupted",
        question: "Please hang this request until I stop it now",
        finished_at: "2026-10-18T12:06:00Z",
      }),
    );
    render(<ResearchRunPage />);
    expect(await screen.findByText("Error code: interrupted")).toBeTruthy();
    expect(screen.getByText("No report is stored for this run.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Streaming notes" })).toBeNull();
  });

  it("shows the not-found message from the API", async () => {
    getRunMock.mockRejectedValue(new ApiError("not_found", "The research run was not found.", 404));
    render(<ResearchRunPage />);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The research run was not found.",
    );
  });

  it("sends an anonymous reader to login", async () => {
    getRunMock.mockRejectedValue(
      new ApiError("unauthenticated", "Authentication is required.", 401),
    );
    render(<ResearchRunPage />);
    await screen.findByText("Loading run.");
    expect(navigation.replace).toHaveBeenCalledWith(
      "/login?next=%2Fresearch%2F11111111-1111-4111-8111-111111111111",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});