"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { errorFromResponse, isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import { getRun, openResearchStream, uploadPdf } from "@/lib/api";
import {
  canStart,
  createSession,
  PDF_LIMIT_BYTES,
  researchSession,
  STALL_MS,
  type SessionState,
} from "@/lib/research-session";
import { isTerminalFrame, parseSse, type SseFrame } from "@/lib/sse";

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function failureMessage(error: unknown): string {
  return isApiError(error) ? error.message : REQUEST_FAILED_MESSAGE;
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
  const go = navigate ?? router.push;
  const [state, dispatch] = useReducer(researchSession, null, () => createSession("pending"));
  const stateRef = useRef(state);
  const fileRef = useRef<File | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    dispatch({ type: "assign_id", researchId: crypto.randomUUID() });
  }, []);

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
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "retry", researchId: crypto.randomUUID() });
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
    const current = stateRef.current;
    if (!canStart(current)) return;
    const token = generation.current + 1;
    generation.current = token;
    const researchId = current.researchId;
    const question = current.question.trim();
    const file = current.indexed ? null : fileRef.current;
    const controller = new AbortController();
    abortRef.current = controller;

    void (async () => {
      if (file) {
        dispatch({ type: "upload_started" });
        try {
          await uploadPdf(researchId, file, controller.signal);
          if (token !== generation.current) return;
          dispatch({ type: "upload_succeeded" });
        } catch (error) {
          if (token !== generation.current) return;
          if (isAbortError(error)) {
            dispatch({ type: "interrupted", code: "interrupted" });
            return;
          }
          dispatch({ type: "upload_failed", message: failureMessage(error) });
          return;
        }
      }
      if (token !== generation.current || controller.signal.aborted) return;
      dispatch({ type: "stream_started", at: Date.now() });
      try {
        const response = await openResearchStream(
          { question, research_id: researchId },
          controller.signal,
        );
        if (token !== generation.current) return;
        if (!response.ok || !response.body) {
          const error = await errorFromResponse(response);
          dispatch({ type: "failed", code: error.code, message: error.message });
          return;
        }
        let terminal = false;
        let runId: string | null = null;
        for await (const frame of parseSse(response.body, controller.signal)) {
          if (token !== generation.current) return;
          if (frame.type === "eof") {
            terminal = frame.terminal;
            break;
          }
          applyFrame(frame, Date.now());
          if (isTerminalFrame(frame)) {
            terminal = true;
            if (frame.type === "event" && frame.name === "research.completed") {
              runId = frame.data.run_id;
            }
            break;
          }
        }
        if (token !== generation.current) return;
        if (!terminal) {
          dispatch({ type: "disconnected" });
          return;
        }
        if (!runId) return;
        try {
          await getRun(runId);
        } catch (error) {
          if (token !== generation.current) return;
          dispatch({ type: "detail_failed", message: failureMessage(error) });
          return;
        }
        if (token !== generation.current) return;
        go(`/research/${runId}`);
      } catch (error) {
        if (token !== generation.current) return;
        if (isAbortError(error)) {
          dispatch({ type: "interrupted", code: "interrupted" });
          return;
        }
        dispatch({ type: "disconnected" });
      }
    })();
  }, [applyFrame, go]);

  return { state, setQuestion, setFile, start, stop, retry };
}
