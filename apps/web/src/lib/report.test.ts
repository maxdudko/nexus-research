import { describe, expect, it } from "vitest";

import { citationView, safeHttpUrl, sourceMap } from "@/lib/report";
import type { EvidenceRef, ResearchReport } from "@/lib/types";

function source(overrides: Partial<EvidenceRef> = {}): EvidenceRef {
  return {
    evidence_id: "E1",
    source_id: "11111111-1111-4111-8111-111111111111",
    chunk_id: "22222222-2222-4222-8222-222222222222",
    research_id: "33333333-3333-4333-8333-333333333333",
    url: "https://example.com/paper",
    page_number: null,
    ...overrides,
  };
}

describe("safeHttpUrl", () => {
  it("keeps http and https URLs", () => {
    expect(safeHttpUrl("https://example.com/paper")).toBe("https://example.com/paper");
    expect(safeHttpUrl("http://example.com/notes")).toBe("http://example.com/notes");
  });

  it("rejects javascript, data, and unparseable values", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,hi")).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});

describe("citationView", () => {
  it("resolves an id only through the server source map", () => {
    const report = { sources: [source(), source({ evidence_id: "E2", url: null, page_number: 4 })] };
    const sources = sourceMap(report as Pick<ResearchReport, "sources">);
    expect(citationView("E1", sources)).toMatchObject({
      kind: "source",
      href: "https://example.com/paper",
      pageLabel: null,
    });
    expect(citationView("E2", sources)).toMatchObject({
      kind: "source",
      href: null,
      pageLabel: "PDF · page 4",
    });
    expect(citationView("E9", sources)).toEqual({ kind: "unknown", evidenceId: "E9" });
  });

  it("does not turn an unsafe URL into a link target", () => {
    const sources = sourceMap({
      sources: [source({ url: "javascript:alert(1)", page_number: 2 })],
    });
    expect(citationView("E1", sources)).toEqual({
      kind: "source",
      evidenceId: "E1",
      href: null,
      pageLabel: "PDF · page 2",
    });
  });
});
