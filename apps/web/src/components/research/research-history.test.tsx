// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchHistory } from "@/components/research/research-history";
import { ApiError } from "@/lib/errors";
import type { RunSummary } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => "/history",
}));

vi.mock("@/lib/api", () => ({
  listRuns: vi.fn(),
  createResearchScope: vi.fn(),
}));

import { createResearchScope, listRuns } from "@/lib/api";

const listRunsMock = vi.mocked(listRuns);
const createResearchScopeMock = vi.mocked(createResearchScope);

function run(id: string, question: string, status: RunSummary["status"] = "completed"): RunSummary {
  return {
    run_id: id,
    research_id: "33333333-3333-4333-8333-333333333333",
    question,
    status,
    error_code: null,
    created_at: "2026-10-18T12:00:00Z",
    finished_at: status === "running" ? null : "2026-10-18T12:01:00Z",
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ResearchHistory", () => {
  it("appends the next page without duplicating a run", async () => {
    const first = run("11111111-1111-4111-8111-111111111111", "How does the first page stream?");
    const overlap = run("22222222-2222-4222-8222-222222222222", "How does the shared row stay unique?");
    const older = run("44444444-4444-4444-8444-444444444444", "How does the older page append?");
    listRunsMock
      .mockResolvedValueOnce({ items: [first, overlap], next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ items: [overlap, older], next_cursor: null });

    render(<ResearchHistory />);
    expect(await screen.findByRole("link", { name: first.question })).toBeTruthy();
    expect(screen.getByRole("link", { name: overlap.question })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Older runs" }));
    expect(await screen.findByRole("link", { name: older.question })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: overlap.question })).toHaveLength(1);
    expect(listRunsMock).toHaveBeenLastCalledWith(20, "cursor-1");
    expect(screen.queryByRole("button", { name: "Older runs" })).toBeNull();
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });

  it("keeps loaded runs when an older page fails", async () => {
    const first = run("11111111-1111-4111-8111-111111111111", "How does the first page stream?");
    listRunsMock
      .mockResolvedValueOnce({ items: [first], next_cursor: "cursor-1" })
      .mockRejectedValueOnce(new ApiError("history_unavailable", "The research service is unavailable.", 503));

    render(<ResearchHistory />);
    expect(await screen.findByRole("link", { name: first.question })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Older runs" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The research service is unavailable.");
    expect(screen.getByRole("link", { name: first.question })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Older runs" })).toBeTruthy();
    expect(screen.queryByText("No research runs yet.")).toBeNull();
  });

  it("labels a stored running row as last recorded", async () => {
    const active = run(
      "11111111-1111-4111-8111-111111111111",
      "What was still marked running?",
      "running",
    );
    listRunsMock.mockResolvedValueOnce({ items: [active], next_cursor: null });
    render(<ResearchHistory />);
    expect(await screen.findByText("Last recorded: Running")).toBeTruthy();
    expect(screen.queryByText("Currently researching")).toBeNull();
  });

  it("retries a failed first page", async () => {
    const finished = run("55555555-5555-4555-8555-555555555555", "How does a finished run appear?");
    listRunsMock
      .mockRejectedValueOnce(new ApiError("history_unavailable", "The research service is unavailable.", 503))
      .mockResolvedValueOnce({ items: [finished], next_cursor: null });
    render(<ResearchHistory />);
    expect((await screen.findByRole("alert")).textContent).toBe("The research service is unavailable.");
    expect(screen.queryByText("No research runs yet.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: finished.question })).toBeTruthy();
    expect(listRunsMock).toHaveBeenNthCalledWith(1, 20);
    expect(listRunsMock).toHaveBeenNthCalledWith(2, 20);
  });
});
