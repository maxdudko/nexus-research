"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { createResearchScope, getRun, openResearchStream, uploadPdf } from "@/lib/api";
import { errorFromResponse, isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import {
  canStart,
  createSession,
  isScopeId,
  PDF_LIMIT_BYTES,
  researchSession,
  STALL_MS,
  type SessionState,
} from "@/lib/research-session";
import { loginHref } from "@/lib/return-path";
import { isTerminalFrame, parseSse, type SseFrame } from "@/lib/sse";
import type { RunDetailResponse, RunStatus } from "@/lib/types";

const RUNNING_UNCONFIRMED = "Last known state: running; execution is not confirmed.";

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function failureMessage(error: unknown): string {
  if (!isApiError(error)) return REQUEST_FAILED_MESSAGE;
  if (error.retryAfter === null || error.message.includes("Try again in ")) return error.message;
  return `${error.message} Try again in ${error.retryAfter} seconds.`;
}

function interruptCode(code: string | null): "interrupted" | "deadline_exceeded" {
  return code === "deadline_exceeded" ? "deadline_exceeded" : "interrupted";
}

function runIdFrom(frame: SseFrame): string | null {
  if (frame.type !== "event") return null;
  if (frame.name === "research.started" || frame.name === "research.completed") {
    return frame.data.run_id;
  }
  return null;
}

export interface ResearchStream {
  state: SessionState;
  setQuestion: (question: string) => void;
  setFile: (file: File | null) => void;
  start: () => void;
  stop: () => void;
  retry: () => void;
}

export function useResearchStream(navigate?: (href: string) => void): ResearchStream {
  const router = useRouter();
  const pathname = usePathname();
  const go = navigate ?? router.push;
  const [state, dispatch] = useReducer(researchSession, null, () => createSession(""));
  const stateRef = useRef(state);
  const fileRef = useRef<File | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const submitLock = useRef(false);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    if (state.phase !== "streaming" && state.phase !== "stalled") return;
    const anchor = state.lastFrameAt ?? state.startedAt;
    if (anchor === null) return;
    const delay = Math.max(0, anchor + STALL_MS - Date.now());
    const timer = window.setTimeout(() => dispatch({ type: "stall" }), delay);
    return () => window.clearTimeout(timer);
  }, [state.phase, state.lastFrameAt, state.startedAt]);

  useEffect(() => {
    return () => {
      generation.current += 1;
      submitLock.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const setQuestion = useCallback((question: string) => {
    dispatch({ type: "edit_question", question });
  }, []);

  const setFile = useCallback((file: File | null) => {
    fileRef.current = file;
    if (!file) {
      dispatch({ type: "clear_file" });
      return;
    }
    if (file.size > PDF_LIMIT_BYTES) {
      dispatch({ type: "file_too_large", name: file.name, size: file.size });
      return;
    }
    dispatch({ type: "select_file", name: file.name, size: file.size });
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(() => {
    generation.current += 1;
    submitLock.current = false;
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "retry" });
  }, []);

  const applyFrame = useCallback((frame: SseFrame, at: number) => {
    if (frame.type === "ping") {
      dispatch({ type: "heartbeat", at });
      return;
    }
    if (frame.type === "unknown" || frame.type === "malformed") {
      dispatch({ type: "activity", at });
      return;
    }
    if (frame.type !== "event") return;
    if (frame.name === "research.started") {
      dispatch({ type: "started", runId: frame.data.run_id, at });
      return;
    }
    if (frame.name === "research.progress") {
      dispatch({
        type: "progress",
        at,
        stage: {
          stage: frame.data.stage,
          iteration: frame.data.iteration,
          message: frame.data.message,
          counts: frame.data.counts,
        },
      });
      return;
    }
    if (frame.name === "research.completed") {
      dispatch({ type: "completed", status: frame.data.status, runId: frame.data.run_id });
      return;
    }
    if (frame.name === "research.failed") {
      dispatch({
        type: "failed",
        code: frame.data.code,
        message: `Research failed (${frame.data.code}).`,
      });
      return;
    }
    dispatch({ type: "interrupted", code: frame.data.code });
  }, []);

  const start = useCallback(() => {
    if (submitLock.current) return;
    const current = stateRef.current;
    if (!canStart(current)) return;
    submitLock.current = true;
    const token = generation.current + 1;
    generation.current = token;
    const question = current.question.trim();
    const file = current.indexed ? null : fileRef.current;
    const reuseScope = current.phase === "upload_failed" && isScopeId(current.researchId);
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    const release = () => {
      if (token === generation.current) submitLock.current = false;
    };
    const expired = () => token !== generation.current;
    const redirectToLogin = () => {
      router.replace(loginHref(pathname));
    };

    void (async () => {
      try {
        let researchId = current.researchId;
        if (!reuseScope) {
          dispatch({ type: "scope_started" });
          try {
            researchId = await createResearchScope(fetch, controller.signal);
          } catch (error) {
            if (expired()) return;
            if (isAbortError(error)) {
              dispatch({ type: "interrupted", code: "interrupted" });
              return;
            }
            if (isApiError(error) && error.status === 401) {
              redirectToLogin();
              return;
            }
            dispatch({
              type: "scope_failed",
              code: isApiError(error) ? error.code : "request_failed",
              message: failureMessage(error),
            });
            return;
          }
          if (expired()) return;
          if (controller.signal.aborted) {
            dispatch({ type: "interrupted", code: "interrupted" });
            return;
          }
          dispatch({ type: "scope_minted", researchId });
        }

        if (file) {
          dispatch({ type: "upload_started" });
          try {
            await uploadPdf(researchId, file, controller.signal);
            if (expired()) return;
            dispatch({ type: "upload_succeeded" });
          } catch (error) {
            if (expired()) return;
            if (isAbortError(error)) {
              dispatch({ type: "interrupted", code: "interrupted" });
              return;
            }
            if (isApiError(error) && error.status === 401) {
              redirectToLogin();
              return;
            }
            dispatch({ type: "upload_failed", message: failureMessage(error) });
            return;
          }
        }

        if (expired() || controller.signal.aborted) return;
        dispatch({ type: "stream_started", at: Date.now() });
        let runId: string | null = null;
        let navigated = false;
        const openStored = (href: string) => {
          if (navigated) return;
          navigated = true;
          go(href);
        };
        const showStored = (status: RunStatus, known: string, errorCode: string | null, navigateOnReport: boolean) => {
          if (status === "completed" || status === "partial" || status === "insufficient_evidence") {
            dispatch({ type: "completed", status, runId: known });
            if (navigateOnReport) openStored(`/research/${known}`);
            return;
          }
          if (status === "failed") {
            const code = errorCode ?? "provider_unavailable";
            dispatch({ type: "failed", code, message: `Research failed (${code}).` });
            return;
          }
          if (status === "interrupted") {
            dispatch({ type: "interrupted", code: interruptCode(errorCode) });
            return;
          }
          dispatch({ type: "disconnected", message: RUNNING_UNCONFIRMED });
        };
        const reconcile = async (navigateOnReport: boolean) => {
          if (!runId) {
            dispatch({ type: "disconnected" });
            return;
          }
          const known = runId;
          let detail: RunDetailResponse;
          try {
            detail = await getRun(known);
          } catch (error) {
            if (expired()) return;
            if (isApiError(error) && error.status === 401) {
              redirectToLogin();
              return;
            }
            dispatch({ type: "disconnected", message: RUNNING_UNCONFIRMED });
            return;
          }
          if (expired()) return;
          showStored(detail.run.status, known, detail.run.error_code, navigateOnReport);
        };

        try {
          const response = await openResearchStream(
            { question, research_id: researchId },
            controller.signal,
          );
          if (expired()) return;
          if (!response.ok || !response.body) {
            const error = await errorFromResponse(response);
            if (expired()) return;
            if (error.status === 401) {
              redirectToLogin();
              return;
            }
            dispatch({ type: "failed", code: error.code, message: error.message });
            return;
          }
          let terminal = false;
          for await (const frame of parseSse(response.body, controller.signal)) {
            if (expired()) return;
            const seen = runIdFrom(frame);
            if (seen) runId = seen;
            if (frame.type === "eof") {
              terminal = frame.terminal;
              break;
            }
            applyFrame(frame, Date.now());
            if (isTerminalFrame(frame)) {
              terminal = true;
              break;
            }
          }
          if (expired()) return;
          if (!terminal) {
            await reconcile(true);
            return;
          }
          if (!runId) return;
          try {
            await getRun(runId);
          } catch (error) {
            if (expired()) return;
            if (isApiError(error) && error.status === 401) {
              redirectToLogin();
              return;
            }
            dispatch({ type: "detail_failed", message: failureMessage(error) });
            return;
          }
          if (expired()) return;
          openStored(`/research/${runId}`);
        } catch (error) {
          if (expired()) return;
          if (isAbortError(error)) {
            if (runId) await reconcile(false);
            else dispatch({ type: "interrupted", code: "interrupted" });
            return;
          }
          await reconcile(true);
        }
      } finally {
        release();
      }
    })();
  }, [applyFrame, go, pathname, router]);

  return { state, setQuestion, setFile, start, stop, retry };
}
