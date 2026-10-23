// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchForm } from "@/components/research/research-form";
import { ResearchProgress } from "@/components/research/research-progress";
import { createSession, PDF_LIMIT_BYTES, type SessionState } from "@/lib/research-session";

const QUESTION = "How does the proxy stream results?";

afterEach(() => {
  cleanup();
});

function startButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Start research" }) as HTMLButtonElement;
}

function renderForm(state: SessionState, onFile = vi.fn()) {
  render(
    <ResearchForm
      state={state}
      onQuestion={vi.fn()}
      onFile={onFile}
      onStart={vi.fn()}
      onStop={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
  return { onFile };
}

describe("research form", () => {
  it("requires a question of 10 to 2000 characters", () => {
    renderForm({ ...createSession("draft-1"), question: "short" });
    expect(startButton().disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("10 and 2000");
  });

  it("shows the selected PDF name and size and refuses files over 10 MB", async () => {
    const onFile = vi.fn();
    renderForm({ ...createSession("draft-1"), question: QUESTION }, onFile);
    const file = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("PDF evidence"), file);
    expect(onFile).toHaveBeenCalledWith(file);
    cleanup();

    const oversized = renderForm({
      ...createSession("draft-1"),
      question: QUESTION,
      phase: "upload_failed",
      fileName: "big.pdf",
      fileSize: PDF_LIMIT_BYTES + 1,
      errorMessage: "This PDF is larger than 10 MB.",
    });
    expect(screen.getByText("big.pdf (10.0 MB)")).toBeTruthy();
    expect(screen.getByText(/no extractable text/)).toBeTruthy();
    expect(screen.getByText(/not kept for download/)).toBeTruthy();
    expect(startButton().disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(oversized.onFile).toHaveBeenCalledWith(null);
  });

  it("disables start and offers stop while a stream is open", () => {
    renderForm({
      ...createSession("draft-1"),
      question: QUESTION,
      phase: "streaming",
      runId: "run-1",
    });
    expect(startButton().disabled).toBe(true);
    expect(
      screen.getByRole("button", {
        name: "Stop this request. Stopping does not pause or resume, it can take a moment, and provider cost may already have been incurred.",
      }),
    ).toBeTruthy();
    expect(screen.getByText(/Closing the tab stops the request/)).toBeTruthy();
  });

  it("lists arrived stages and the stalled state without a percent", () => {
    const state: SessionState = {
      ...createSession("draft-1"),
      question: QUESTION,
      phase: "stalled",
      startedAt: Date.now() - 50_000,
      lastFrameAt: Date.now() - 46_000,
      stages: [
        {
          stage: "planning",
          iteration: 0,
          message: "Planning the research",
          counts: { pages_indexed: 0, search_calls: 0 },
        },
      ],
    };
    render(<ResearchProgress state={state} />);
    expect(screen.getByText(/Planning the research/)).toBeTruthy();
    expect(screen.getByText("No update for 45 seconds. The request is still open.")).toBeTruthy();
    expect(screen.getByText("Status:").textContent).toContain("Stalled");
    expect(document.body.textContent).not.toContain("%");
    const stages = screen.getByRole("list");
    expect(stages.getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText(/Last heartbeat/).closest("[aria-live]")).toBeNull();
    expect(screen.getByText(/Elapsed/).closest("[aria-live]")).toBeNull();
  });
});
