"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { getRun } from "@/lib/api";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import type { RunDetailResponse } from "@/lib/types";

export default function ResearchRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = typeof params.runId === "string" ? params.runId : "";
  const [detail, setDetail] = useState<RunDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    getRun(runId).then(
      (body) => {
        if (!cancelled) setDetail(body);
      },
      (cause: unknown) => {
        if (!cancelled) setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8">
      <p>
        <Link href="/" className="underline">
          Back to research
        </Link>
      </p>
      {error ? (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      ) : null}
      {!detail && !error ? <p>Loading run.</p> : null}
      {detail ? (
        <article className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">{detail.run.question}</h1>
          <p>Status: {detail.run.status}</p>
          {detail.run.status === "running" ? (
            <p>Last known state: running. Execution is not confirmed.</p>
          ) : null}
          {detail.run.error_code ? <p>Error code: {detail.run.error_code}</p> : null}
          {detail.report ? (
            <section className="flex flex-col gap-2">
              <h2 className="text-lg font-semibold">{detail.report.title}</h2>
              <p>{detail.report.summary}</p>
            </section>
          ) : (
            <p>No report is stored for this run.</p>
          )}
        </article>
      ) : null}
    </main>
  );
}
