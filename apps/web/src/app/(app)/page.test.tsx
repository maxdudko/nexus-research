// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(app)/page";
import { ApiError } from "@/lib/errors";
import type { RunSummary } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => "/",
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

describe("dashboard", () => {
  it("shows an empty state and does not mint a scope", async () => {
    listRunsMock.mockResolvedValueOnce({ items: [], next_cursor: null });
    render(<DashboardPage />);
    expect(screen.getByRole("link", { name: "New research" }).getAttribute("href")).toBe("/research/new");
    expect(screen.getByText(/not proof the report is correct/)).toBeTruthy();
    expect(screen.getByText(/only while it is open/)).toBeTruthy();
    expect(await screen.findByText("No research runs yet.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "View history" })).toBeNull();
    expect(listRunsMock).toHaveBeenCalledWith(5);
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });

  it("shows one recent run without a history link", async () => {
    const only = run("11111111-1111-4111-8111-111111111111", "How does one saved run appear?");
    listRunsMock.mockResolvedValueOnce({ items: [only], next_cursor: null });
    render(<DashboardPage />);
    const link = await screen.findByRole("link", { name: only.question });
    expect(link.className).toContain("line-clamp-2");
    expect(link.className).toContain("break-words");
    expect(screen.queryByRole("link", { name: "View history" })).toBeNull();
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });

  it("shows five runs, a running label, and a link to full history", async () => {
    const items = [
      run("11111111-1111-4111-8111-111111111111", "How does the newest run read?", "running"),
      run("22222222-2222-4222-8222-222222222222", "How does the second run read?"),
      run("33333333-3333-4333-8333-333333333333", "How does the third run read?"),
      run("44444444-4444-4444-8444-444444444444", "How does the fourth run read?"),
      run("55555555-5555-4555-8555-555555555555", "How does a very long question wrap inside the recent list without stretching the page?"),
    ];
    listRunsMock.mockResolvedValueOnce({ items, next_cursor: "cursor-1" });
    render(<DashboardPage />);
    expect(await screen.findByText("Last recorded: Running")).toBeTruthy();
    expect(screen.getAllByRole("link").length).toBeGreaterThanOrEqual(6);
    expect(screen.getByRole("link", { name: "View history" }).getAttribute("href")).toBe("/history");
    expect(listRunsMock).toHaveBeenCalledWith(5);
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });

  it("retries a failed recent list without showing an empty state", async () => {
    const only = run("11111111-1111-4111-8111-111111111111", "How does one saved run appear?");
    listRunsMock
      .mockRejectedValueOnce(new ApiError("history_unavailable", "The research service is unavailable.", 503))
      .mockResolvedValueOnce({ items: [only], next_cursor: null });
    render(<DashboardPage />);
    expect((await screen.findByRole("alert")).textContent).toBe("The research service is unavailable.");
    expect(screen.queryByText("No research runs yet.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: only.question })).toBeTruthy();
    expect(createResearchScopeMock).not.toHaveBeenCalled();
  });
});
