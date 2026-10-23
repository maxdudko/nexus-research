"use client";

import { useEffect, useState } from "react";

import type { SessionPhase, SessionState } from "@/lib/research-session";

const STATUS_LABEL: Record<SessionPhase, string> = {
  idle: "Ready",
  creating_scope: "Preparing the research",
  scope_failed: "Could not start",
  uploading: "Indexing PDF",
  upload_failed: "Upload failed",
  streaming: "Researching",
  stalled: "Stalled",
  completed: "Completed",
  partial: "Partial",
  insufficient_evidence: "Insufficient evidence",
  failed: "Failed",
  interrupted: "Interrupted",
  disconnected: "Disconnected",
};

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes === 0) return `${rest}s`;
  return `${minutes}m ${rest}s`;
}

export function ResearchProgress({ state }: { state: SessionState }) {
  const active = state.phase === "streaming" || state.phase === "stalled";
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  return (
    <section aria-labelledby="progress-heading" aria-busy={active} className="flex flex-col gap-3">
      <h2 id="progress-heading" className="text-lg font-semibold">
        Progress
      </h2>
      <p>
        Status: <span data-status={state.phase}>{STATUS_LABEL[state.phase]}</span>
      </p>
      {state.runId ? <p>Run {state.runId}</p> : null}
      {active && state.startedAt !== null ? (
        <p>Elapsed {formatElapsed(now - state.startedAt)}</p>
      ) : null}
      {active ? (
        <p>
          Last heartbeat{" "}
          {state.lastHeartbeatAt === null
            ? "not yet"
            : `${formatElapsed(now - state.lastHeartbeatAt)} ago`}
        </p>
      ) : null}
      {state.phase === "stalled" ? (
        <p role="status">No update for 45 seconds. The request is still open.</p>
      ) : null}
      {state.retryNotice ? <p role="status">{state.retryNotice}</p> : null}
      {state.errorMessage && state.phase !== "upload_failed" ? (
        <p role="alert" className="text-sm text-danger">
          {state.errorMessage}
        </p>
      ) : null}
      <ol aria-live="polite" className="flex list-decimal flex-col gap-2 pl-5">
        {state.stages.map((stage, index) => (
          <li key={`${stage.stage}-${stage.iteration}-${index}`}>
            {stage.message}
            {` · ${stage.counts.pages_indexed} pages indexed`}
          </li>
        ))}
      </ol>
    </section>
  );
}
