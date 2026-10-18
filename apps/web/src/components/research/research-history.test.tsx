// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchHistory } from "@/components/research/research-history";
import type { RunSummary } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  listRuns: vi.fn(),
}));

import { listRuns } from "@/lib/api";

const listRunsMock = vi.mocked(listRuns);

function run(id: string, question: string): RunSummary {
  return {
    run_id: id,
    research_id: "33333333-3333-4333-8333-333333333333",
    question,
    status: "completed",
    error_code: null,
    created_at: "2026-10-18T12:00:00Z",
    finished_at: "2026-10-18T12:01:00Z",
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
  });

  it("replaces the first page when the refresh key changes", async () => {
    const first = run("11111111-1111-4111-8111-111111111111", "How does the first page stream?");
    const finished = run("55555555-5555-4555-8555-555555555555", "How does a finished run appear?");
    listRunsMock
      .mockResolvedValueOnce({ items: [first], next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ items: [finished], next_cursor: null });

    const { rerender } = render(<ResearchHistory refreshKey={0} />);
    expect(await screen.findByRole("link", { name: first.question })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Older runs" })).toBeTruthy();
    rerender(<ResearchHistory refreshKey={1} />);
    expect(await screen.findByRole("link", { name: finished.question })).toBeTruthy();
    expect(screen.queryByRole("link", { name: first.question })).toBeNull();
    expect(screen.queryByRole("button", { name: "Older runs" })).toBeNull();
    expect(listRunsMock).toHaveBeenNthCalledWith(1, 20);
    expect(listRunsMock).toHaveBeenNthCalledWith(2, 20);
  });
});
