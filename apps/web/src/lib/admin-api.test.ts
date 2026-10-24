import { afterEach, describe, expect, it, vi } from "vitest";

import { getUsage, listEvaluationCases, listEvaluations } from "@/lib/admin-api";
import { ApiError } from "@/lib/errors";

const ID = "11111111-1111-4111-8111-111111111111";

function item(extra: Record<string, unknown> = {}) {
  return {
    id: ID,
    suite: "offline",
    outcome: "passed",
    imported_at: "2026-10-24T12:00:00+00:00",
    executed_at: null,
    dataset_version: "2026-10-19.2",
    dataset_sha256: "ab".repeat(32),
    report_version: "1",
    scorer_version: "1",
    k: 5,
    code_revision: null,
    code_revision_source: null,
    provenance_level: "offline_recomputed",
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
    question: "Canary question about private research sources.",
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("admin api", () => {
  it("requests history with no-store and drops fields that are not on the allowlist", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      Response.json({ items: [item()], next_cursor: null }),
    );
    const page = await listEvaluations(20, undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/admin/evaluations?limit=20", { cache: "no-store" });
    expect(page.items[0]?.retrieval.recall_at_k).toBe("0.100000");
    expect(JSON.stringify(page)).not.toContain("Canary question");
  });

  it("drops rationale from a case result", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      Response.json({
        items: [
          {
            category: "retrieval",
            case_id: "q01-single",
            result: {
              gold_count: 1,
              recall_at_k: "1.000000",
              precision_at_k: "0.200000",
              mrr_at_k: "1.000000",
              rationale: "SENTINEL_RATIONALE",
            },
          },
        ],
        next_cursor: null,
      }),
    );
    const page = await listEvaluationCases(ID, 20, undefined, fetchImpl);
    expect(JSON.stringify(page)).not.toContain("SENTINEL_RATIONALE");
    expect(page.items[0]?.result.recall_at_k).toBe("1.000000");
  });

  it("keeps usage cost unknown and drops a planted note", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      Response.json({
        window_start: "2026-10-17T12:00:00+00:00",
        window_end: "2026-10-24T12:00:00+00:00",
        runs_with_usage: 1,
        runs_without_usage: 1,
        counters: {
          iterations: 2,
          search_calls: 1,
          llm_calls: 4,
          pages_indexed: 3,
          pages_failed: 1,
        },
        cost_estimate: null,
        coverage: "unavailable",
        pricing_version: null,
        note: "CANARY_USAGE_TOKENS",
      }),
    );
    const summary = await getUsage(undefined, undefined, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/admin/usage", { cache: "no-store" });
    expect(summary.cost_estimate).toBeNull();
    expect(summary.counters.llm_calls).toBe(4);
    expect(JSON.stringify(summary)).not.toContain("CANARY_USAGE_TOKENS");
    const priced = vi.fn().mockResolvedValue(
      Response.json({
        window_start: "2026-10-17T12:00:00+00:00",
        window_end: "2026-10-24T12:00:00+00:00",
        runs_with_usage: 1,
        runs_without_usage: 0,
        counters: { iterations: 0, search_calls: 0, llm_calls: 1, pages_indexed: 0, pages_failed: 0 },
        cost_estimate: 0,
        coverage: "unavailable",
        pricing_version: null,
      }),
    );
    await expect(getUsage(undefined, undefined, priced)).rejects.toThrow(ApiError);
  });
});
