import { describe, expect, it } from "vitest";

import {
  createSession,
  PDF_LIMIT_BYTES,
  researchSession,
  type SessionState,
} from "@/lib/research-session";

const QUESTION = "How does the proxy stream results?";

function streaming(state: SessionState = createSession("draft-1")): SessionState {
  return researchSession(
    { ...state, question: QUESTION },
    { type: "stream_started", at: 1_000 },
  );
}

describe("researchSession", () => {
  it("keeps one research id for the PDF and the run", () => {
    let state = createSession("draft-1");
    state = researchSession(state, { type: "edit_question", question: QUESTION });
    state = researchSession(state, { type: "select_file", name: "notes.pdf", size: 1200 });
    state = researchSession(state, { type: "upload_started" });
    state = researchSession(state, { type: "upload_succeeded" });
    state = researchSession(state, { type: "stream_started", at: 1_000 });
    expect(state.researchId).toBe("draft-1");
    expect(state.indexed).toBe(true);
    expect(state.phase).toBe("streaming");
  });

  it("ignores a second start while a stream is open", () => {
    const state = streaming();
    const again = researchSession(state, { type: "stream_started", at: 2_000 });
    expect(again).toBe(state);
  });

  it("records stop as interrupted and a dropped stream as disconnected", () => {
    const stopped = researchSession(streaming(), { type: "interrupted", code: "interrupted" });
    expect(stopped.phase).toBe("interrupted");
    expect(stopped.phase).not.toBe("completed");

    const dropped = researchSession(streaming(), { type: "disconnected" });
    expect(dropped.phase).toBe("disconnected");
    const stillDropped = researchSession(dropped, { type: "disconnected" });
    expect(stillDropped.phase).toBe("disconnected");
  });

  it("keeps partial and insufficient evidence distinct from completed", () => {
    const partial = researchSession(streaming(), {
      type: "completed",
      status: "partial",
      runId: "run-1",
    });
    const thin = researchSession(streaming(), {
      type: "completed",
      status: "insufficient_evidence",
      runId: "run-2",
    });
    expect(partial.phase).toBe("partial");
    expect(thin.phase).toBe("insufficient_evidence");
    expect(researchSession(partial, { type: "disconnected" }).phase).toBe("partial");
  });

  it("stalls without adding a stage and resumes when a frame arrives", () => {
    const open = streaming();
    const stalled = researchSession(open, { type: "stall" });
    expect(stalled.phase).toBe("stalled");
    expect(stalled.stages).toEqual([]);
    const resumed = researchSession(stalled, {
      type: "progress",
      at: 50_000,
      stage: {
        stage: "planning",
        iteration: 0,
        message: "Planning the research",
        counts: { pages_indexed: 0, search_calls: 0 },
      },
    });
    expect(resumed.phase).toBe("streaming");
    expect(resumed.stages).toHaveLength(1);
  });

  it("refuses an oversized PDF and starts a new id on retry", () => {
    let state = researchSession(createSession("draft-1"), {
      type: "edit_question",
      question: QUESTION,
    });
    state = researchSession(state, {
      type: "file_too_large",
      name: "big.pdf",
      size: PDF_LIMIT_BYTES + 1,
    });
    expect(state.phase).toBe("upload_failed");
    expect(state.errorMessage).toBe("This PDF is larger than 10 MB.");
    const retried = researchSession(state, { type: "retry", researchId: "draft-2" });
    expect(retried.researchId).toBe("draft-2");
    expect(retried.phase).toBe("idle");
    expect(retried.indexed).toBe(false);
    expect(retried.retryNotice).toBe("This is a new run. The previous run is not resumed.");
    expect(retried.question).toBe(QUESTION);
  });

  it("stays on the detail error instead of navigating as success", () => {
    const done = researchSession(streaming(), {
      type: "completed",
      status: "completed",
      runId: "run-1",
    });
    const failed = researchSession(done, {
      type: "detail_failed",
      message: "History is unavailable.",
    });
    expect(failed.phase).toBe("failed");
    expect(failed.errorMessage).toBe("History is unavailable.");
  });
});
