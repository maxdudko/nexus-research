"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ResearchForm } from "@/components/research/research-form";
import { ResearchProgress } from "@/components/research/research-progress";
import { useResearchStream } from "@/hooks/use-research-stream";
import { listRuns } from "@/lib/api";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import type { RunSummary } from "@/lib/types";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function ResearchWorkspace() {
  const stream = useResearchStream();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRuns(20).then(
      (page) => {
        if (!cancelled) setRuns(page.items);
      },
      (cause: unknown) => {
        if (!cancelled) setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
      },
    ).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-8">
      <ResearchForm
        state={stream.state}
        onQuestion={stream.setQuestion}
        onFile={stream.setFile}
        onStart={stream.start}
        onStop={stream.stop}
        onRetry={stream.retry}
      />
      <ResearchProgress state={stream.state} />
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
          {runs.map((run) => (
            <li key={run.run_id} className="rounded border border-neutral-300 bg-white p-3">
              <Link href={`/research/${run.run_id}`} className="font-medium underline">
                {run.question}
              </Link>
              <p className="mt-1 text-sm text-neutral-700">
                {run.status} · {formatTime(run.created_at)}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
