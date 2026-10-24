// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvaluationCompare } from "@/components/admin/evaluation-compare";
import { EvaluationDetail } from "@/components/admin/evaluation-detail";
import { EvaluationHistory } from "@/components/admin/evaluation-history";
import { SessionIdentity } from "@/components/auth/session-boundary";
import { ApiError } from "@/lib/errors";
import type { EvaluationComparison, EvaluationDetail as Detail, EvaluationListItem } from "@/lib/admin-api";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  pathname: "/admin/evaluations",
  search: "",
  id: "11111111-1111-4111-8111-111111111111",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(navigation.search),
  useParams: () => ({ id: navigation.id }),
}));

vi.mock("@/lib/admin-api", () => ({
  listEvaluations: vi.fn(),
  getEvaluation: vi.fn(),
  listEvaluationCases: vi.fn(),
  compareEvaluations: vi.fn(),
}));

import { compareEvaluations, getEvaluation, listEvaluationCases, listEvaluations } from "@/lib/admin-api";

const listMock = vi.mocked(listEvaluations);
const detailMock = vi.mocked(getEvaluation);
const casesMock = vi.mocked(listEvaluationCases);
const compareMock = vi.mocked(compareEvaluations);

const LEFT = "11111111-1111-4111-8111-111111111111";
const RIGHT = "22222222-2222-4222-8222-222222222222";
const LIVE = "33333333-3333-4333-8333-333333333333";

function item(id: string, overrides: Partial<EvaluationListItem> = {}): EvaluationListItem {
  return {
    id,
    suite: "offline",
    outcome: "passed",
    imported_at: "2026-10-24T12:00:00+00:00",
    executed_at: null,
    dataset_version: "2026-10-19.2",
    dataset_sha256: "ab".repeat(32),
    report_version: "1",
    scorer_version: "1",
    k: 5,
    code_revision: "a".repeat(40),
    code_revision_source: "operator_asserted",
    provenance_level: "offline_recomputed",
    is_committed_baseline: id === LEFT,
    model: null,
    prompt_version: null,
    temperature: null,
    pricing_version: null,
    retrieval: {
      recall_at_k: id === RIGHT ? "0.400000" : "0.100000",
      precision_at_k: "0.250000",
      mrr_at_k: id === RIGHT ? "0.500000" : null,
      defined_cases: 2,
      undefined_cases: 1,
    },
    citations: { cases: 1, valid: 1, invalid: 0 },
    grounding: { cases: 1, structurally_valid: 1, structurally_invalid: 0 },
    counts: null,
    agreement: null,
    compared: null,
    agreements: null,
    ...overrides,
  };
}

function detail(id: string, overrides: Partial<Detail> = {}): Detail {
  return {
    ...item(id),
    config: { empty_gold: "undefined", metric_quant: "0.000001", label_taxonomy: null },
    calibration: null,
    ...overrides,
  };
}

const LABELS = ["supported", "unsupported", "insufficient_context", "contradictory"];

function liveDetail(): Detail {
  const matrix = Object.fromEntries(LABELS.map((gold) => [gold, Object.fromEntries(LABELS.map((predicted) => [predicted, 0]))]));
  matrix.supported.supported = 11;
  return detail(LIVE, {
    suite: "live",
    outcome: "informational",
    provenance_level: "operator_imported",
    is_committed_baseline: false,
    model: "gpt-4o-mini",
    prompt_version: "grounding-judge-2",
    temperature: "0.000",
    pricing_version: "2026-10-19",
    counts: { attempted: 13, succeeded: 13, failed: 0, skipped: 2, disagreed: 2 },
    agreement: "0.846154",
    compared: 13,
    agreements: 11,
    config: {
      empty_gold: "undefined",
      metric_quant: "0.000001",
      label_taxonomy: LABELS,
    },
    calibration: {
      labels: LABELS,
      matrix,
      compared: 13,
      agreements: 11,
      agreement: "0.846154",
    },
  });
}

function comparison(comparable: boolean): EvaluationComparison {
  return {
    comparable,
    reasons: comparable ? [] : ["dataset_mismatch"],
    warnings: ["execution_time_unknown"],
    cost_comparable: false,
    pricing_reason: "pricing_unavailable",
    left: detail(LEFT),
    right: detail(RIGHT),
    deltas: comparable
      ? {
          retrieval: {
            recall_at_k: "0.300000",
            precision_at_k: "0.000000",
            mrr_at_k: null,
            defined_cases: 0,
            undefined_cases: 0,
          },
          citations: { cases: 0, valid: 0, invalid: 0 },
          grounding: { cases: 0, structurally_valid: 0, structurally_invalid: 0 },
          agreement: null,
          counts: null,
          matrix: null,
        }
      : null,
  };
}

function renderAs(role: string, ui: ReactNode) {
  return render(
    <SessionIdentity.Provider value={{ id: LEFT, email: "ada@example.com", role }}>{ui}</SessionIdentity.Provider>,
  );
}

afterEach(() => {
  cleanup();
  listMock.mockReset();
  detailMock.mockReset();
  casesMock.mockReset();
  compareMock.mockReset();
  localStorage.clear();
  sessionStorage.clear();
  navigation.replace.mockReset();
  navigation.push.mockReset();
  navigation.pathname = "/admin/evaluations";
  navigation.search = "";
  navigation.id = LEFT;
});

describe("evaluation explorer", () => {
  it("hides admin data from a signed-in user and does not request it", async () => {
    renderAs("user", <EvaluationHistory />);
    expect((await screen.findByRole("alert")).textContent).toBe("The request was rejected.");
    expect(listMock).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("lists evaluations, paginates from the keyboard, and opens compare", async () => {
    const older = item(RIGHT);
    listMock
      .mockResolvedValueOnce({ items: [item(LEFT)], next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ items: [older], next_cursor: null });
    const user = userEvent.setup();
    renderAs("admin", <EvaluationHistory />);
    expect(await screen.findByText("Frozen fixture regression")).toBeTruthy();
    expect(screen.getByText("Committed baseline")).toBeTruthy();
    expect(screen.getByText("Execution time unknown")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Confusion matrix" })).toBeNull();
    const button = screen.getByRole("button", { name: "Load older evaluations" });
    button.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("link", { name: `Open ${RIGHT}` })).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: `Select ${LEFT}` }));
    await user.click(screen.getByRole("checkbox", { name: `Select ${RIGHT}` }));
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(navigation.push).toHaveBeenCalledWith(
      `/admin/evaluations/compare?left_id=${LEFT}&right_id=${RIGHT}`,
    );
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows an empty history and retries an unavailable service", async () => {
    listMock.mockResolvedValueOnce({ items: [], next_cursor: null });
    renderAs("admin", <EvaluationHistory />);
    expect(await screen.findByText("No evaluations have been imported.")).toBeTruthy();
    cleanup();
    listMock.mockRejectedValueOnce(new ApiError("history_unavailable", "hidden", 503));
    listMock.mockResolvedValueOnce({ items: [], next_cursor: null });
    const user = userEvent.setup();
    renderAs("admin", <EvaluationHistory />);
    expect((await screen.findByRole("alert")).textContent).toBe("The research service is unavailable.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No evaluations have been imported.")).toBeTruthy();
  });

  it("shows an offline detail without a matrix and a live matrix without rationale", async () => {
    detailMock.mockResolvedValueOnce(detail(LEFT));
    casesMock.mockResolvedValueOnce({
      items: [
        {
          category: "retrieval",
          case_id: "q01-single",
          result: {
            gold_count: 1,
            recall_at_k: "1.000000",
            precision_at_k: "0.200000",
            mrr_at_k: "1.000000",
          },
        },
      ],
      next_cursor: null,
    });
    navigation.id = LEFT;
    renderAs("admin", <EvaluationDetail />);
    expect(await screen.findByText("Dataset fingerprint " + "ab".repeat(32))).toBeTruthy();
    expect(screen.getByRole("table", { name: "Evaluation cases" })).toBeTruthy();
    expect(screen.getByText("q01-single")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Confusion matrix" })).toBeNull();
    expect(document.body.textContent).not.toContain("SENTINEL_RATIONALE");
    cleanup();

    detailMock.mockResolvedValueOnce(liveDetail());
    casesMock.mockResolvedValueOnce({
      items: [
        {
          category: "grounding",
          case_id: "g01-supported",
          result: {
            structural_valid: true,
            expect_structurally_valid: true,
            gold_label: "supported",
            error_code: null,
            excerpt_count: 1,
            predicted_label: "supported",
            outcome: "ok",
            model: "gpt-4o-mini",
            prompt_version: "grounding-judge-2",
          },
        },
      ],
      next_cursor: null,
    });
    navigation.id = LIVE;
    renderAs("admin", <EvaluationDetail />);
    expect(await screen.findByRole("table", { name: "Confusion matrix" })).toBeTruthy();
    expect(screen.getByText("Informational live judge")).toBeTruthy();
    expect(screen.getByRole("cell", { name: "11" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("SENTINEL_RATIONALE");
  });

  it("shows deltas for a compatible pair and reasons without deltas for an incompatible pair", async () => {
    compareMock.mockResolvedValueOnce(comparison(true));
    navigation.search = `left_id=${LEFT}&right_id=${RIGHT}`;
    navigation.pathname = "/admin/evaluations/compare";
    renderAs("admin", <EvaluationCompare />);
    expect(await screen.findByRole("table", { name: "Metric deltas" })).toBeTruthy();
    expect(screen.getByText("0.300000")).toBeTruthy();
    expect(screen.getAllByText("Execution time unknown").length).toBeGreaterThan(0);
    expect(screen.getByText("Pricing version unavailable.")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Confusion matrix" })).toBeNull();
    expect(document.body.textContent).not.toContain("$");
    cleanup();

    compareMock.mockResolvedValueOnce(comparison(false));
    renderAs("admin", <EvaluationCompare />);
    expect((await screen.findByRole("alert")).textContent).toContain("Dataset mismatch");
    expect(screen.queryByRole("table", { name: "Metric deltas" })).toBeNull();
    expect(screen.getAllByText("Frozen fixture regression")).toHaveLength(2);
    cleanup();

    const live = liveDetail();
    compareMock.mockResolvedValueOnce({
      ...comparison(true),
      left: live,
      right: live,
    });
    renderAs("admin", <EvaluationCompare />);
    expect(await screen.findAllByRole("table", { name: "Confusion matrix" })).toHaveLength(2);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("returns a signed-out admin to the allowlisted evaluations path", async () => {
    listMock.mockRejectedValueOnce(new ApiError("unauthenticated", "Authentication is required.", 401));
    renderAs("admin", <EvaluationHistory />);
    await vi.waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Fadmin%2Fevaluations");
    });
  });
});
