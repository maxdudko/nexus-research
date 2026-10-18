// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ResearchReportView } from "@/components/research/research-report";
import type { EvidenceRef, ResearchReport } from "@/lib/types";

afterEach(() => {
  cleanup();
});

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

function report(overrides: Partial<ResearchReport> = {}): ResearchReport {
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
    limitations: [],
    status: "completed",
    sources: [source()],
    ...overrides,
  };
}

describe("ResearchReportView", () => {
  it("links an https citation and refuses javascript and data URLs", () => {
    const { unmount } = render(<ResearchReportView report={report()} />);
    const link = screen.getByRole("link", { name: "E1" });
    expect(link.getAttribute("href")).toBe("https://example.com/paper");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("target")).toBe("_blank");
    unmount();

    render(
      <ResearchReportView
        report={report({
          sections: [
            {
              heading: "Unsafe",
              content: "These addresses stay text.",
              citations: ["E1", "E2"],
            },
          ],
          sources: [
            source({ url: "javascript:alert(1)" }),
            source({ evidence_id: "E2", url: "data:text/html,hi" }),
          ],
        })}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("E1")).toBeTruthy();
    expect(screen.getByText("E2")).toBeTruthy();
  });

  it("shows a PDF page as text and an unknown id without a link", () => {
    render(
      <ResearchReportView
        report={report({
          sections: [
            {
              heading: "Sources",
              content: "One page and one missing id.",
              citations: ["E1", "E9"],
            },
          ],
          sources: [source({ url: null, page_number: 4 })],
        })}
      />,
    );
    expect(
      screen.getByText(/PDF · page 4\. The uploaded file is not stored, so this page cannot be opened\./),
    ).toBeTruthy();
    expect(screen.getByText("Unknown source (E9)")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("renders report text without executing markup", () => {
    render(
      <ResearchReportView
        report={report({
          title: "<script>alert(1)</script>",
          summary: "<img src=x onerror=alert(1)>",
        })}
      />,
    );
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("<script>alert(1)</script>");
    expect(document.body.querySelector("script")).toBeNull();
    expect(document.body.querySelector("img")).toBeNull();
  });

  it("keeps partial limitations visible", () => {
    render(
      <ResearchReportView
        report={report({
          status: "partial",
          limitations: ["Only one source was retrieved."],
        })}
      />,
    );
    expect(screen.getByText("Status: Partial")).toBeTruthy();
    expect(screen.getByText("Only one source was retrieved.")).toBeTruthy();
  });
});
