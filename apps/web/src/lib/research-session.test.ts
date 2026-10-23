import { describe, expect, it } from "vitest";

import {
  canStart,
  canStop,
  createSession,
  PDF_LIMIT_BYTES,
  researchSession,
  type SessionState,
} from "@/lib/research-session";

const SCOPE = "22222222-2222-4222-8222-222222222222";

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

  it("refuses an oversized PDF and clears the scope id on retry", () => {
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
    expect(state.researchId).toBe("");
    expect(canStart(state)).toBe(false);
    expect(state.errorMessage).toBe("This PDF is larger than 10 MB.");
    const retried = researchSession(state, { type: "retry" });
    expect(retried.researchId).toBe("");
    expect(retried.phase).toBe("idle");
    expect(retried.indexed).toBe(false);
    expect(retried.retryNotice).toBe("This is a new run. The previous run is not resumed.");
    expect(retried.question).toBe(QUESTION);
  });

  it("forgets an indexed PDF when a retry keeps the selected file", () => {
    let state = researchSession(createSession("draft-1"), {
      type: "edit_question",
      question: QUESTION,
    });
    state = researchSession(state, { type: "select_file", name: "notes.pdf", size: 1200 });
    state = researchSession(state, { type: "upload_started" });
    state = researchSession(state, { type: "upload_succeeded" });
    expect(state.indexed).toBe(true);
    const retried = researchSession(state, { type: "retry" });
    expect(retried.researchId).toBe("");
    expect(retried.indexed).toBe(false);
    expect(retried.fileName).toBe("notes.pdf");
    expect(retried.fileSize).toBe(1200);
  });

  it("lets Start mint a scope, and retries a failed index on the same scope", () => {
    const ready = researchSession(createSession(""), { type: "edit_question", question: QUESTION });
    expect(canStart(ready)).toBe(true);
    expect(canStart({ ...ready, question: "short" })).toBe(false);

    const creating = researchSession(ready, { type: "scope_started" });
    expect(creating.phase).toBe("creating_scope");
    expect(canStop(creating.phase)).toBe(true);
    expect(canStart(creating)).toBe(false);
    const minted = researchSession(creating, { type: "scope_minted", researchId: SCOPE });
    expect(minted.researchId).toBe(SCOPE);
    expect(minted.phase).toBe("creating_scope");

    const scopeFailed = researchSession(creating, {
      type: "scope_failed",
      code: "too_many_requests",
      message: "Too many research runs are in progress.",
    });
    expect(scopeFailed.phase).toBe("scope_failed");
    expect(scopeFailed.researchId).toBe("");
    expect(canStart(scopeFailed)).toBe(true);

    const failedUpload = { ...ready, researchId: SCOPE, phase: "upload_failed" as const };
    expect(canStart(failedUpload)).toBe(true);
    const changed = researchSession(failedUpload, {
      type: "select_file",
      name: "replacement.pdf",
      size: 1200,
    });
    expect(changed.phase).toBe("idle");
    expect(changed.researchId).toBe("");
    expect(changed.fileName).toBe("replacement.pdf");
    expect(canStart(changed)).toBe(true);
    const cleared = researchSession(failedUpload, { type: "clear_file" });
    expect(cleared.phase).toBe("idle");
    expect(cleared.researchId).toBe("");
    expect(cleared.fileName).toBeNull();
    const edited = researchSession(failedUpload, {
      type: "edit_question",
      question: "How does the same PDF stay on this scope?",
    });
    expect(edited.phase).toBe("upload_failed");
    expect(edited.researchId).toBe(SCOPE);
    expect(canStart(edited)).toBe(true);
    const oversized = researchSession(failedUpload, {
      type: "file_too_large",
      name: "big.pdf",
      size: PDF_LIMIT_BYTES + 1,
    });
    expect(oversized.phase).toBe("upload_failed");
    expect(oversized.researchId).toBe("");
    expect(oversized.fileName).toBe("big.pdf");
    expect(canStart(oversized)).toBe(false);
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
