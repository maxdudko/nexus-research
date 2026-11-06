// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchReportView } from "@/components/research/research-report";
import { ResearchForm } from "@/components/research/research-form";
import { createSession } from "@/lib/research-session";
import type { ResearchReport, ReportBlock } from "@/lib/types";

afterEach(cleanup);

const paragraph: ReportBlock = { kind: "fact", text: "The value was 10 kg in 2025.", citations: ["E1"], columns: [], rows: [] };
const report: ResearchReport = {
  format_version: 2, verification: "passed", title: "Measurements", summary: paragraph.text,
  summary_blocks: [paragraph], status: "completed", limitations: [],
  sections: [{ heading: "Results", content: "Measurements", citations: ["E1"], blocks: [
    { kind: "table", text: "Annual values", citations: ["E1"], columns: ["Year", "Mass (kg)"], rows: [["2025", "10"]] },
    { ...paragraph, kind: "inference", text: "This comparison is limited to 2025." },
  ] }],
  sources: [{ evidence_id: "E1", source_id: "source", chunk_id: "chunk", research_id: "research",
    url: "https://example.org/report", page_number: null, title: "Annual measurements", domain: "example.org",
    excerpt: "The 2025 measurement was 10 kg.", published_at: null, retrieved_at: "2026-11-06T12:00:00Z" }],
};

describe("Detailed research reports", () => {
  it("shows a contents link, table, qualified inference and the exact cited excerpt", () => {
    render(<ResearchReportView report={report} />);
    expect(screen.getByRole("navigation", { name: "Report contents" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Mass (kg)" })).toBeTruthy();
    expect(screen.getByText("Analytical inference")).toBeTruthy();
    const passage = screen.getAllByText("Read cited passage (E1)")[0];
    fireEvent.click(passage);
    expect(passage.closest("details")?.open).toBe(true);
    expect(screen.getAllByText("The 2025 measurement was 10 kg.").length).toBeGreaterThan(0);
    expect(screen.getByText("Annual measurements")).toBeTruthy();
    expect(screen.getByText(/Published: unknown/)).toBeTruthy();
  });

  it("keeps an incomplete support check visible", () => {
    render(<ResearchReportView report={{ ...report, status: "partial", verification: "incomplete" }} />);
    expect(screen.getByRole("status").textContent).toBe("Partial");
    expect(screen.getByText(/Support checking is incomplete/)).toBeTruthy();
  });

  it("selects deep mode and locks the control during a run", () => {
    const onDepth = vi.fn();
    const props = { state: createSession(""), onQuestion: vi.fn(), onFile: vi.fn(),
      onStart: vi.fn(), onStop: vi.fn(), onRetry: vi.fn(), onDepth };
    const { rerender } = render(<ResearchForm {...props} />);
    fireEvent.change(screen.getByLabelText("Research depth"), { target: { value: "deep" } });
    expect(onDepth).toHaveBeenCalledWith("deep");
    rerender(<ResearchForm {...props} state={{ ...props.state, depth: "deep", phase: "streaming" }} />);
    expect((screen.getByLabelText("Research depth") as HTMLSelectElement).disabled).toBe(true);
  });
});

describe("Report body completeness", () => {
  it("identifies a stored summary-only report even when its old status says completed", () => {
    render(<ResearchReportView report={{ ...report, body_gaps: [], sections: [{
      heading: "Scope and Method", content: "Method only", citations: [],
      blocks: [{ kind: "context", text: "Method only", citations: [], columns: [], rows: [] }],
    }] }} />);
    expect(screen.getByRole("status").textContent).toBe("Partial");
    expect(screen.getByText("Report body is incomplete")).toBeTruthy();
    expect(screen.getByText(/The summary does not replace the full analysis/)).toBeTruthy();
  });

  it("renders every analytical section alongside the summary and contents", () => {
    render(<ResearchReportView report={{ ...report, body_gaps: [], sections: [
      ...report.sections,
      { heading: "Limitations of the measurements", content: "Limited to 2025.", citations: ["E1"],
        blocks: [{ ...paragraph, text: "The evidence covers the 2025 period only." }] },
      { heading: "Comparison", content: "A separate analysis.", citations: ["E1"],
        blocks: [{ ...paragraph, text: "A separate analytical finding." }] },
    ] }} />);
    expect(screen.getByRole("heading", { name: "Summary" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Results" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Limitations of the measurements" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Comparison" })).toBeTruthy();
    expect(screen.getByText("A separate analytical finding.")).toBeTruthy();
    expect(screen.queryByText("Report body is incomplete")).toBeNull();
  });
});
