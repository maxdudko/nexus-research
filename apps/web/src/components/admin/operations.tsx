"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { provenanceBadge } from "@/components/admin/evaluation-view";
import { useSessionIdentity } from "@/components/auth/session-boundary";
import {
  getDiagnostics,
  getOverview,
  getUsage,
  listDiagnosticRuns,
  type DiagnosticRun,
  type DiagnosticsSummary,
  type EvaluationListItem,
  type UsageSummary,
} from "@/lib/admin-api";
import { isApiError } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";

function preset(days: number): string {
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  return new URLSearchParams({
    from: start.toISOString(),
    to: end.toISOString(),
  }).toString();
}

function WindowLinks({ path }: { path: string }) {
  return (
    <p className="flex flex-wrap gap-3 text-sm">
      <a className="underline" href={`${path}?${preset(7)}`}>
        Last 7 days
      </a>
      <a className="underline" href={`${path}?${preset(90)}`}>
        Last 90 days
      </a>
    </p>
  );
}

function Rejected() {
  return (
    <p role="alert" className="text-sm text-danger">
      The request was rejected.
    </p>
  );
}

function Unavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <p role="alert">The research service is unavailable.</p>
      <button
        type="button"
        className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
        onClick={onRetry}
      >
        Try again
      </button>
    </div>
  );
}

function useAdminFailure(
  allowed: boolean,
  cause: unknown,
  pathname: string,
): "ok" | "forbidden" | "unavailable" | "login" {
  const router = useRouter();
  useEffect(() => {
    if (!allowed || !isApiError(cause) || cause.status !== 401) return;
    router.replace(loginHref(pathname));
  }, [allowed, cause, pathname, router]);
  if (!allowed) return "forbidden";
  if (!isApiError(cause)) return cause ? "unavailable" : "ok";
  if (cause.status === 401) return "login";
  if (cause.status === 403) return "forbidden";
  return "unavailable";
}

function Counts({ summary }: { summary: DiagnosticsSummary }) {
  const rows = [
    ["Running", summary.statuses.running],
    ["Completed", summary.statuses.completed],
    ["Partial", summary.statuses.partial],
    ["Insufficient evidence", summary.statuses.insufficient_evidence],
    ["Failed", summary.statuses.failed],
    ["Interrupted", summary.statuses.interrupted],
    ["Recorded success", summary.statuses.recorded_success],
    ["Recorded failure", summary.statuses.recorded_failure],
  ] as const;
  return (
    <dl className="grid grid-cols-2 gap-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function RecordedState({ summary }: { summary: DiagnosticsSummary }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p>Stale running: {summary.stale_running}. Last recorded state.</p>
      <p>Running without a start time: {summary.running_without_start}. Last recorded state.</p>
    </div>
  );
}

function LatestEvaluation({ item }: { item: EvaluationListItem | null }) {
  if (!item) return <p>No evaluations recorded.</p>;
  return (
    <article className="flex flex-col gap-1 text-sm">
      <p>{provenanceBadge(item)}</p>
      <p>{item.dataset_version}</p>
      <p>Execution time unknown</p>
      <p>Imported {item.imported_at}</p>
    </article>
  );
}

export function OverviewPanel() {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const pathname = usePathname();
  const [summary, setSummary] = useState<DiagnosticsSummary | null>(null);
  const [latest, setLatest] = useState<EvaluationListItem | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const gate = useAdminFailure(allowed, failure, pathname);

  useEffect(() => {
    if (!allowed) return;
    let cancelled = false;
    getOverview()
      .then((body) => {
        if (cancelled) return;
        setSummary(body.diagnostics);
        setLatest(body.latest_evaluation);
        setFailure(null);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setFailure(cause);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt]);

  if (gate === "forbidden") return <Rejected />;
  if (gate === "login") return <p role="status">Sign-in is required.</p>;
  if (!summary) {
    if (gate === "unavailable") return <Unavailable onRetry={() => setAttempt((value) => value + 1)} />;
    return <p role="status">Loading overview.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <p>Operational aggregate</p>
      <p>
        Window {summary.window_start} to {summary.window_end} UTC
      </p>
      <Counts summary={summary} />
      <RecordedState summary={summary} />
      <LatestEvaluation item={latest} />
    </div>
  );
}

export function DiagnosticsPanel() {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const pathname = usePathname();
  const params = useSearchParams();
  const from = params.get("from") ?? undefined;
  const to = params.get("to") ?? undefined;
  const [summary, setSummary] = useState<DiagnosticsSummary | null>(null);
  const [runs, setRuns] = useState<DiagnosticRun[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const gate = useAdminFailure(allowed, failure, pathname);

  useEffect(() => {
    if (!allowed) return;
    let cancelled = false;
    Promise.all([getDiagnostics(from, to), listDiagnosticRuns(from, to)])
      .then(([body, page]) => {
        if (cancelled) return;
        setSummary(body);
        setRuns(page.items);
        setCursor(page.next_cursor);
        setFailure(null);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setFailure(cause);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt, from, to]);

  if (gate === "forbidden") return <Rejected />;
  if (gate === "login") return <p role="status">Sign-in is required.</p>;
  if (!summary) {
    if (gate === "unavailable") return <Unavailable onRetry={() => setAttempt((value) => value + 1)} />;
    return <p role="status">Loading diagnostics.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <p>Operational aggregate</p>
      <p>
        Window {summary.window_start} to {summary.window_end} UTC
      </p>
      <WindowLinks path="/admin/diagnostics" />
      <Counts summary={summary} />
      <RecordedState summary={summary} />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Error</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id}>
                <td>{run.run_id}</td>
                <td>{run.status}</td>
                <td>{run.error_code ?? ""}</td>
                <td>{run.duration ?? "Unknown"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cursor ? (
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={() => {
            void listDiagnosticRuns(from, to, 20, cursor).then((page) => {
              setRuns((current) => [...current, ...page.items]);
              setCursor(page.next_cursor);
            });
          }}
        >
          Older runs
        </button>
      ) : null}
    </div>
  );
}

export function UsagePanel() {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const pathname = usePathname();
  const params = useSearchParams();
  const from = params.get("from") ?? undefined;
  const to = params.get("to") ?? undefined;
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const gate = useAdminFailure(allowed, failure, pathname);

  useEffect(() => {
    if (!allowed) return;
    let cancelled = false;
    getUsage(from, to)
      .then((body) => {
        if (cancelled) return;
        setSummary(body);
        setFailure(null);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setFailure(cause);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt, from, to]);

  if (gate === "forbidden") return <Rejected />;
  if (gate === "login") return <p role="status">Sign-in is required.</p>;
  if (!summary) {
    if (gate === "unavailable") return <Unavailable onRetry={() => setAttempt((value) => value + 1)} />;
    return <p role="status">Loading usage.</p>;
  }
  const counters = [
    ["Iterations", summary.counters.iterations],
    ["Search calls", summary.counters.search_calls],
    ["Model calls", summary.counters.llm_calls],
    ["Pages indexed", summary.counters.pages_indexed],
    ["Pages failed", summary.counters.pages_failed],
  ] as const;
  return (
    <div className="flex flex-col gap-4">
      <p>Operational aggregate</p>
      <p>
        Window {summary.window_start} to {summary.window_end} UTC
      </p>
      <WindowLinks path="/admin/usage" />
      <p>These counts are logical graph calls. They are not provider tokens, SDK retries, or a bill.</p>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        {counters.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <p>Cost unknown.</p>
      <p>Coverage unavailable.</p>
    </div>
  );
}
