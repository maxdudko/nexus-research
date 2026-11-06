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
