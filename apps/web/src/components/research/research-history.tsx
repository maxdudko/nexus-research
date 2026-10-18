"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listRuns } from "@/lib/api";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import type { RunStatus, RunSummary } from "@/lib/types";

const STATUS_LABEL: Record<RunStatus, string> = {
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  insufficient_evidence: "Insufficient evidence",
  failed: "Failed",
  interrupted: "Interrupted",
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function dedupe(items: RunSummary[]): RunSummary[] {
  const seen = new Set<string>();
  const unique: RunSummary[] = [];
  for (const item of items) {
    if (seen.has(item.run_id)) continue;
    seen.add(item.run_id);
    unique.push(item);
  }
  return unique;
}

export function ResearchHistory() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRuns(20)
      .then((page) => {
        if (cancelled) return;
        setRuns(dedupe(page.items));
        setCursor(page.next_cursor);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadOlder(): Promise<void> {
    if (!cursor || loadingOlder) return;
    setLoadingOlder(true);
    setError(null);
    try {
      const page = await listRuns(20, cursor);
      setRuns((current) => dedupe([...current, ...page.items]));
      setCursor(page.next_cursor);
    } catch (cause: unknown) {
      setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
    } finally {
      setLoadingOlder(false);
    }
  }

  return (
    <section aria-labelledby="recent-heading" className="flex flex-col gap-3">
      <h2 id="recent-heading" className="text-lg font-semibold">
        Recent runs
      </h2>
      {loading ? <p>Loading recent runs.</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      ) : null}
      {!loading && !error && runs.length === 0 ? <p>No research runs yet.</p> : null}
      <ul className="flex flex-col gap-3">
        {runs.map((run) => {
          const prominent = run.status === "partial" || run.status === "insufficient_evidence";
          return (
            <li key={run.run_id} className="rounded border border-neutral-300 bg-white p-3">
              <Link href={`/research/${run.run_id}`} className="line-clamp-2 break-words font-medium underline">
                {run.question}
              </Link>
              <p className="mt-1 text-sm text-neutral-700">
                <span className={prominent ? "font-semibold" : undefined}>{STATUS_LABEL[run.status]}</span>
                {" · "}
                {formatTime(run.created_at)}
              </p>
            </li>
          );
        })}
      </ul>
      {cursor ? (
        <button
          type="button"
          className="rounded border border-neutral-900 px-4 py-2 disabled:cursor-not-allowed disabled:text-neutral-400"
          disabled={loadingOlder}
          onClick={() => {
            void loadOlder();
          }}
        >
          {loadingOlder ? "Loading older runs." : "Older runs"}
        </button>
      ) : null}
    </section>
  );
}
