// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AdminLayout from "@/app/(app)/admin/layout";
import { DiagnosticsPanel, OverviewPanel, UsagePanel } from "@/components/admin/operations";
import { SessionIdentity } from "@/components/auth/session-boundary";
import { ApiError } from "@/lib/errors";
import type { DiagnosticsSummary, EvaluationListItem, UsageSummary } from "@/lib/admin-api";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  pathname: "/admin",
  search: "",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

vi.mock("@/lib/admin-api", () => ({
  getOverview: vi.fn(),
  getDiagnostics: vi.fn(),
  getUsage: vi.fn(),
  listDiagnosticRuns: vi.fn(),
}));

import { getDiagnostics, getOverview, getUsage, listDiagnosticRuns } from "@/lib/admin-api";

const overviewMock = vi.mocked(getOverview);
const diagnosticsMock = vi.mocked(getDiagnostics);
const usageMock = vi.mocked(getUsage);
const runsMock = vi.mocked(listDiagnosticRuns);

const RUN = "11111111-1111-4111-8111-111111111111";

function summary(overrides: Partial<DiagnosticsSummary> = {}): DiagnosticsSummary {
  return {
    window_start: "2026-10-17T12:00:00+00:00",
    window_end: "2026-10-24T12:00:00+00:00",
    statuses: {
      running: 0,
      completed: 0,
      partial: 0,
      insufficient_evidence: 0,
      failed: 0,
      interrupted: 0,
      recorded_success: 0,
      recorded_failure: 0,
      recorded_running: 0,
    },
    stale_running: 0,
    running_without_start: 0,
    errors: [],
    duration: { known_count: 0, unknown_count: 0, total_seconds: null, max_seconds: null },
    ...overrides,
  };
}

function usage(llmCalls = 0): UsageSummary {
  return {
    window_start: "2026-10-17T12:00:00+00:00",
    window_end: "2026-10-24T12:00:00+00:00",
    runs_with_usage: llmCalls === 0 ? 0 : 1,
    runs_without_usage: 0,
    counters: {
      iterations: 0,
      search_calls: 0,
      llm_calls: llmCalls,
      pages_indexed: 0,
      pages_failed: 0,
    },
    cost_estimate: null,
    coverage: "unavailable",
    pricing_version: null,
  };
}

function item(suite: "offline" | "live"): EvaluationListItem {
  return {
    id: RUN,
    suite,
    outcome: suite === "offline" ? "passed" : "informational",
    imported_at: "2026-10-24T12:00:00+00:00",
    executed_at: null,
    dataset_version: "2026-10-19.2",
    dataset_sha256: "ab".repeat(32),
    report_version: "1",
    scorer_version: "1",
    k: 5,
    code_revision: null,
    code_revision_source: null,
    provenance_level: suite === "offline" ? "offline_recomputed" : "operator_imported",
    is_committed_baseline: false,
    model: null,
    prompt_version: null,
    temperature: null,
    pricing_version: null,
    retrieval: {
      recall_at_k: "0.100000",
      precision_at_k: "0.250000",
      mrr_at_k: null,
      defined_cases: 2,
      undefined_cases: 1,
    },
    citations: { cases: 1, valid: 1, invalid: 0 },
    grounding: { cases: 1, structurally_valid: 1, structurally_invalid: 0 },
    counts: null,
    agreement: null,
    compared: null,
    agreements: null,
  };
}

function renderAdmin(role: string, ui: ReactNode) {
  return render(
    <SessionIdentity.Provider value={{ id: RUN, email: "ada@example.com", role }}>{ui}</SessionIdentity.Provider>,
  );
}

afterEach(() => {
  cleanup();
  overviewMock.mockReset();
  diagnosticsMock.mockReset();
  usageMock.mockReset();
  runsMock.mockReset();
  navigation.replace.mockReset();
  navigation.pathname = "/admin";
  navigation.search = "";
  localStorage.clear();
  sessionStorage.clear();
});

describe("operational admin pages", () => {
  it("does not call the admin API for a non-admin", () => {
    renderAdmin(
      "user",
      <AdminLayout>
        <OverviewPanel />
      </AdminLayout>,
    );
    expect(screen.getByRole("alert").textContent).toBe("The request was rejected.");
    expect(overviewMock).not.toHaveBeenCalled();
    expect(diagnosticsMock).not.toHaveBeenCalled();
    expect(usageMock).not.toHaveBeenCalled();
  });

  it("shows an empty overview, a stale count, and the latest evaluation card", async () => {
    overviewMock.mockResolvedValueOnce({
      diagnostics: summary({ stale_running: 2, running_without_start: 1 }),
      latest_evaluation: item("offline"),
    });
    renderAdmin("admin", <OverviewPanel />);
    expect(await screen.findByText("Operational aggregate")).toBeTruthy();
    expect(screen.getByText("Stale running: 2. Last recorded state.")).toBeTruthy();
    expect(screen.getByText("Running without a start time: 1. Last recorded state.")).toBeTruthy();
    expect(screen.getByText("Frozen fixture regression")).toBeTruthy();
    expect(screen.getByText("Execution time unknown")).toBeTruthy();
    expect(document.body.textContent).not.toContain("$0");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows an empty diagnostics window and an unknown duration", async () => {
    navigation.pathname = "/admin/diagnostics";
    diagnosticsMock.mockResolvedValueOnce(summary());
    runsMock.mockResolvedValueOnce({
      items: [
        {
          run_id: RUN,
          status: "running",
          error_code: null,
          created_at: "2026-10-24T11:00:00+00:00",
          started_at: null,
          finished_at: null,
          duration: null,
        },
      ],
      next_cursor: null,
    });
    renderAdmin("admin", <DiagnosticsPanel />);
    expect(await screen.findByText("Last 7 days")).toBeTruthy();
    expect(screen.getByText("Last 90 days")).toBeTruthy();
    expect(screen.getByText("Window 2026-10-17T12:00:00+00:00 to 2026-10-24T12:00:00+00:00 UTC")).toBeTruthy();
    expect(screen.getByRole("cell", { name: "Unknown" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: RUN }).tagName).toBe("TD");
    expect(document.body.textContent).not.toContain("$0");
  });

  it("shows logical usage counts and unknown cost", async () => {
    navigation.pathname = "/admin/usage";
    usageMock.mockResolvedValueOnce(usage(4));
    renderAdmin("admin", <UsagePanel />);
    expect(await screen.findByText("Cost unknown.")).toBeTruthy();
    expect(screen.getByText("Coverage unavailable.")).toBeTruthy();
    expect(screen.getByText(/logical graph calls/)).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(document.body.textContent).not.toContain("$0");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("sends a later 401 to the login return path", async () => {
    overviewMock.mockRejectedValueOnce(new ApiError("unauthenticated", "Authentication is required.", 401));
    renderAdmin("admin", <OverviewPanel />);
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Fadmin");
    });
  });

  it("shows the unavailable state for a network failure", async () => {
    overviewMock.mockRejectedValueOnce(new Error("offline"));
    renderAdmin("admin", <OverviewPanel />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "The research service is unavailable.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
