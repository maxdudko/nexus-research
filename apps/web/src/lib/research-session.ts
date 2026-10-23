import type { ReportStatus, ResearchProgress } from "@/lib/types";

export const PDF_LIMIT_BYTES = 10 * 1024 * 1024;
export const STALL_MS = 45_000;
export const QUESTION_MIN = 10;
export const QUESTION_MAX = 2000;

export type SessionPhase =
  | "idle"
  | "creating_scope"
  | "scope_failed"
  | "uploading"
  | "upload_failed"
  | "streaming"
  | "stalled"
  | "completed"
  | "partial"
  | "insufficient_evidence"
  | "failed"
  | "interrupted"
  | "disconnected";

export interface StageView {
  stage: ResearchProgress["stage"];
  iteration: number;
  message: string;
  counts: ResearchProgress["counts"];
}

export interface SessionState {
  researchId: string;
  question: string;
  phase: SessionPhase;
  runId: string | null;
  stages: StageView[];
  errorMessage: string | null;
  errorCode: string | null;
  fileName: string | null;
  fileSize: number | null;
  indexed: boolean;
  startedAt: number | null;
  lastFrameAt: number | null;
  lastHeartbeatAt: number | null;
  retryNotice: string | null;
}

export type SessionAction =
  | { type: "edit_question"; question: string }
  | { type: "select_file"; name: string; size: number }
  | { type: "clear_file" }
  | { type: "file_too_large"; name: string; size: number }
  | { type: "upload_started" }
  | { type: "upload_succeeded" }
  | { type: "upload_failed"; message: string }
  | { type: "stream_started"; at: number }
  | { type: "started"; runId: string; at: number }
  | { type: "progress"; stage: StageView; at: number }
  | { type: "heartbeat"; at: number }
  | { type: "activity"; at: number }
  | { type: "stall" }
  | { type: "completed"; status: ReportStatus; runId: string }
  | { type: "failed"; code: string; message: string }
  | { type: "interrupted"; code: string }
  | { type: "disconnected"; message?: string }
  | { type: "detail_failed"; message: string }
  | { type: "scope_started" }
  | { type: "scope_minted"; researchId: string }
  | { type: "scope_failed"; code: string; message: string }
  | { type: "retry" };

const BUSY: ReadonlySet<SessionPhase> = new Set([
  "creating_scope",
  "uploading",
  "streaming",
  "stalled",
]);

const SCOPE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function isScopeId(value: string): boolean {
  return SCOPE_ID.test(value);
}

const RETRYABLE: ReadonlySet<SessionPhase> = new Set([
  "completed",
  "partial",
  "insufficient_evidence",
  "failed",
  "interrupted",
  "disconnected",
]);

export function createSession(researchId: string): SessionState {
  return {
    researchId,
    question: "",
    phase: "idle",
    runId: null,
    stages: [],
    errorMessage: null,
    errorCode: null,
    fileName: null,
    fileSize: null,
    indexed: false,
    startedAt: null,
    lastFrameAt: null,
    lastHeartbeatAt: null,
    retryNotice: null,
  };
}

export function questionError(question: string): string | null {
  const length = question.trim().length;
  if (length < QUESTION_MIN || length > QUESTION_MAX) {
    return "Enter a question between 10 and 2000 characters.";
  }
  return null;
}

export function canStart(
  state: Pick<SessionState, "phase" | "question" | "fileSize" | "researchId">,
): boolean {
  if (questionError(state.question)) return false;
  if (state.fileSize !== null && state.fileSize > PDF_LIMIT_BYTES) return false;
  if (state.phase === "idle" || state.phase === "scope_failed") return true;
  if (state.phase === "upload_failed") return isScopeId(state.researchId);
  return false;
}

export function canStop(phase: SessionPhase): boolean {
  return BUSY.has(phase);
}

export function canRetry(phase: SessionPhase): boolean {
  return RETRYABLE.has(phase);
}

function resumeFromStall(state: SessionState, at: number): SessionState {
  return {
    ...state,
    phase: state.phase === "stalled" ? "streaming" : state.phase,
    lastFrameAt: at,
  };
}

export function researchSession(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "edit_question":
      if (BUSY.has(state.phase)) return state;
      return { ...state, question: action.question };
    case "select_file":
      if (BUSY.has(state.phase)) return state;
      return {
        ...abandonFailedUpload(state),
        fileName: action.name,
        fileSize: action.size,
        indexed: false,
        errorMessage: null,
      };
    case "clear_file":
      if (BUSY.has(state.phase)) return state;
      return {
        ...abandonFailedUpload(state),
        fileName: null,
        fileSize: null,
        indexed: false,
        errorMessage: null,
      };
    case "file_too_large":
      if (BUSY.has(state.phase)) return state;
      return {
        ...abandonFailedUpload(state),
        phase: "upload_failed",
        researchId: "",
        fileName: action.name,
        fileSize: action.size,
        indexed: false,
        errorMessage: "This PDF is larger than 10 MB.",
      };
    case "upload_started":
      if (
        state.phase !== "idle" &&
        state.phase !== "upload_failed" &&
        state.phase !== "creating_scope"
      ) {
        return state;
      }
      return { ...state, phase: "uploading", errorMessage: null, errorCode: null };
    case "upload_succeeded":
      return { ...state, indexed: true, errorMessage: null };
    case "upload_failed":
      return {
        ...state,
        phase: "upload_failed",
        indexed: false,
        errorMessage: action.message,
      };
    case "stream_started":
      if (
        state.phase !== "idle" &&
        state.phase !== "upload_failed" &&
        state.phase !== "uploading" &&
        state.phase !== "creating_scope"
      ) {
        return state;
      }
      return {
        ...state,
        phase: "streaming",
        runId: null,
        stages: [],
        errorMessage: null,
        errorCode: null,
        startedAt: action.at,
        lastFrameAt: action.at,
        lastHeartbeatAt: null,
        retryNotice: null,
      };
    case "started":
      if (state.phase !== "streaming" && state.phase !== "stalled") return state;
      return { ...resumeFromStall(state, action.at), runId: action.runId };
    case "progress":
      if (state.phase !== "streaming" && state.phase !== "stalled") return state;
      return {
        ...resumeFromStall(state, action.at),
        stages: [...state.stages, action.stage],
      };
    case "heartbeat":
      if (state.phase !== "streaming" && state.phase !== "stalled") return state;
      return { ...resumeFromStall(state, action.at), lastHeartbeatAt: action.at };
    case "activity":
      if (state.phase !== "streaming" && state.phase !== "stalled") return state;
      return resumeFromStall(state, action.at);
    case "stall":
      if (state.phase !== "streaming") return state;
      return { ...state, phase: "stalled" };
    case "completed":
      if (state.phase !== "streaming" && state.phase !== "stalled") return state;
      return {
        ...state,
        phase: action.status,
        runId: action.runId,
        errorMessage: null,
        errorCode: null,
      };
    case "failed":
      return {
        ...state,
        phase: "failed",
        errorCode: action.code,
        errorMessage: action.message,
      };
    case "interrupted":
      if (state.phase === "completed" || state.phase === "partial") return state;
      if (state.phase === "insufficient_evidence") return state;
      return {
        ...state,
        phase: "interrupted",
        errorCode: action.code,
        errorMessage: `Research was interrupted (${action.code}).`,
      };
    case "disconnected":
      if (
        state.phase === "completed" ||
        state.phase === "partial" ||
        state.phase === "insufficient_evidence" ||
        state.phase === "failed" ||
        state.phase === "interrupted"
      ) {
        return state;
      }
      return {
        ...state,
        phase: "disconnected",
        errorMessage: action.message ?? "The connection closed before research finished.",
      };
    case "detail_failed":
      return { ...state, phase: "failed", errorMessage: action.message };
    case "scope_started":
      if (state.phase !== "idle" && state.phase !== "scope_failed") return state;
      return {
        ...state,
        phase: "creating_scope",
        errorMessage: null,
        errorCode: null,
        runId: null,
        stages: [],
        startedAt: null,
        lastFrameAt: null,
        lastHeartbeatAt: null,
        retryNotice: null,
      };
    case "scope_minted":
      if (state.phase !== "creating_scope") return state;
      return { ...state, researchId: action.researchId };
    case "scope_failed":
      if (state.phase !== "creating_scope") return state;
      return {
        ...state,
        phase: "scope_failed",
        researchId: "",
        errorCode: action.code,
        errorMessage: action.message,
      };
    case "retry":
      return {
        ...createSession(""),
        question: state.question,
        fileName: state.fileName,
        fileSize: state.fileSize,
        retryNotice: "This is a new run. The previous run is not resumed.",
      };
  }
}

function abandonFailedUpload(state: SessionState): SessionState {
  if (state.phase === "upload_failed") {
    return { ...state, phase: "idle", researchId: "" };
  }
  if (state.phase === "scope_failed") return { ...state, phase: "idle" };
  return state;
}
