"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ResearchReportView } from "@/components/research/research-report";
import { getRun } from "@/lib/api";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";
import type { GraphUsage, RunDetail, RunDetailResponse, RunStatus } from "@/lib/types";

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

function usageText(usage: GraphUsage): string {
  return [
    `Iterations ${usage.iterations}`,
    `Search calls ${usage.search_calls}`,
    `Model calls ${usage.llm_calls}`,
    `Pages indexed ${usage.pages_indexed}`,
    `Pages failed ${usage.pages_failed}`,
  ].join(" · ");
}

function timestampLine(run: RunDetail): string {
  const parts = [`Created ${formatTime(run.created_at)}`, `Updated ${formatTime(run.updated_at)}`];
  if (run.finished_at) parts.push(`Finished ${formatTime(run.finished_at)}`);
  return parts.join(" · ");
}

function absentReport(status: RunStatus): string {
  if (status === "failed") return "This run failed. No report is stored.";
  if (status === "interrupted") return "This run was interrupted. No report is stored.";
  return "No report is stored for this run.";
}

function RunDetailView({ runId }: { runId: string }) {
  const router = useRouter();
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
        if (cancelled) return;
        if (isApiError(cause) && cause.status === 401) {
          router.replace(loginHref(`/research/${runId}`));
          return;
        }
        setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [runId, router]);

  const prominent =
    detail?.run.status === "partial" || detail?.run.status === "insufficient_evidence";

  if (error) {
    return (
      <p role="alert" className="text-sm text-danger">
        {error}
      </p>
    );
  }
  if (!detail) {
    return <p role="status">Loading run.</p>;
  }

  return (
    <article className="flex flex-col gap-4">
      <h1 className="break-words text-2xl font-semibold">{detail.run.question}</h1>
      <p className={prominent ? "font-semibold" : undefined}>Status: {STATUS_LABEL[detail.run.status]}</p>
      <p>{timestampLine(detail.run)}</p>
      {detail.run.status === "running" ? (
        <p>
          {`Last known state: running; execution is not confirmed. Updated ${formatTime(detail.run.updated_at)}.`}
        </p>
      ) : null}
      {detail.run.error_code ? <p>Error code: {detail.run.error_code}</p> : null}
      {detail.run.warnings.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">Warnings</h2>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {detail.run.warnings.map((warning) => (
              <li key={warning} className="break-words">
                {warning}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {detail.run.usage ? <p>{usageText(detail.run.usage)}</p> : null}
      {detail.report ? (
        <ResearchReportView report={detail.report} />
      ) : (
        <p>{absentReport(detail.run.status)}</p>
      )}
    </article>
  );
}

export default function ResearchRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = typeof params.runId === "string" ? params.runId : "";

  return (
    <div className="flex flex-col gap-6">
      <p className="flex flex-wrap gap-x-4 gap-y-1">
        <Link href="/history" className="underline">
          Back to history
        </Link>
        <Link href="/" className="underline">
          Dashboard
        </Link>
      </p>
      <RunDetailView key={runId} runId={runId} />
    </div>
  );
}
