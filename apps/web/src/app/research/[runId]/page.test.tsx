// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ResearchRunPage from "@/app/(app)/research/[runId]/page";
import { ApiError } from "@/lib/errors";
import type { ResearchReport, RunDetailResponse } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  runId: "11111111-1111-4111-8111-111111111111",
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ runId: navigation.runId }),
  useRouter: () => navigation,
}));

vi.mock("@/lib/api", () => ({
  getRun: vi.fn(),
}));

import { getRun } from "@/lib/api";

const getRunMock = vi.mocked(getRun);
const RUN = "11111111-1111-4111-8111-111111111111";

function storedReport(status: ResearchReport["status"] = "completed"): ResearchReport {
  return {
    title: "Streaming notes",
    summary: "The first frame arrived before the run finished.",
    sections: [
      {
        heading: "Proxy",
        content: "The browser receives the first event before completion.",
        citations: ["E1"],
      },
    ],
    limitations: ["Only one source was retrieved."],
    status,
    sources: [
      {
        evidence_id: "E1",
        source_id: "11111111-1111-4111-8111-111111111111",
        chunk_id: "22222222-2222-4222-8222-222222222222",
        research_id: "33333333-3333-4333-8333-333333333333",
        url: "https://example.com/paper",
        page_number: null,
      },
    ],
  };
}

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
  navigation.runId = RUN;
});

describe("ResearchRunPage", () => {
  it("shows a stale running row with its timestamp", async () => {
    getRunMock.mockResolvedValue(detail());
    render(<ResearchRunPage />);
    const note = await screen.findByText(/Last known state: running; execution is not confirmed/);
    expect(note.textContent).toContain(new Date("2026-10-18T12:05:00Z").toLocaleString());
    expect(screen.getByText("No report is stored for this run.")).toBeTruthy();
    expect(document.body.textContent).toContain(
      `Created ${new Date("2026-10-18T12:00:00Z").toLocaleString()}`,
    );
    expect(document.body.textContent).toContain(
      `Updated ${new Date("2026-10-18T12:05:00Z").toLocaleString()}`,
    );
    expect(screen.getByRole("link", { name: "Back to history" }).getAttribute("href")).toBe("/history");
    expect(screen.getByRole("link", { name: "Dashboard" }).getAttribute("href")).toBe("/");
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
    expect(screen.getByText("Status: Interrupted")).toBeTruthy();
    expect(screen.getByText("This run was interrupted. No report is stored.")).toBeTruthy();
    expect(document.body.textContent).toContain(
      `Finished ${new Date("2026-10-18T12:06:00Z").toLocaleString()}`,
    );
    expect(screen.queryByRole("heading", { level: 2, name: "Streaming notes" })).toBeNull();
  });

  it("shows a failed run without inventing a report", async () => {
    getRunMock.mockResolvedValue(
      detail({
        status: "failed",
        error_code: "provider_unavailable",
        question: "Why did this run fail?",
        finished_at: "2026-10-18T12:06:00Z",
      }),
    );
    render(<ResearchRunPage />);
    expect(await screen.findByText("Status: Failed")).toBeTruthy();
    expect(screen.getByText("This run failed. No report is stored.")).toBeTruthy();
    expect(screen.queryByText("Streaming notes")).toBeNull();
  });

  it.each([
    ["completed", "Completed"],
    ["partial", "Partial"],
    ["insufficient_evidence", "Insufficient evidence"],
  ] as const)("shows a stored %s report", async (status, label) => {
    getRunMock.mockResolvedValue(
      detail(
        { status, question: "How does the proxy stream results?", finished_at: "2026-10-18T12:06:00Z" },
        storedReport(status),
      ),
    );
    render(<ResearchRunPage />);
    expect(await screen.findByRole("heading", { level: 1, name: "How does the proxy stream results?" })).toBeTruthy();
    expect(screen.getAllByText(`Status: ${label}`).length).toBe(status === "completed" ? 1 : 2);
    expect(screen.getByText(/does not prove the report is correct/)).toBeTruthy();
    expect(screen.getByText("Only one source was retrieved.")).toBeTruthy();
    expect(screen.queryByText("No report is stored for this run.")).toBeNull();
  });

  it("keeps a long question in the heading", async () => {
    const question = `How does ${"x".repeat(1800)} wrap?`;
    getRunMock.mockResolvedValue(detail({ status: "completed", question }));
    render(<ResearchRunPage />);
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(question);
    expect(heading.className).toContain("break-words");
    expect(screen.getByText("No report is stored for this run.")).toBeTruthy();
  });

  it("drops the previous question while the next run loads", async () => {
    let resolveNext: (value: RunDetailResponse) => void = () => undefined;
    getRunMock.mockResolvedValueOnce(detail({ question: "What was still running?" }));
    getRunMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveNext = resolve;
        }),
    );
    const { rerender } = render(<ResearchRunPage />);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveProperty(
      "textContent",
      "What was still running?",
    );
    navigation.runId = "22222222-2222-4222-8222-222222222222";
    rerender(<ResearchRunPage />);
    expect(screen.queryByText("What was still running?")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Loading run.");
    await act(async () => {
      resolveNext(
        detail({
          run_id: "22222222-2222-4222-8222-222222222222",
          question: "How does the next run read?",
          status: "completed",
        }),
      );
    });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveProperty(
      "textContent",
      "How does the next run read?",
    );
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
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("shows a service outage without the question", async () => {
    getRunMock.mockRejectedValue(
      new ApiError("history_unavailable", "The research service is unavailable.", 503),
    );
    render(<ResearchRunPage />);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The research service is unavailable.",
    );
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});