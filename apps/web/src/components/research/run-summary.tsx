import Link from "next/link";

import type { RunStatus, RunSummary } from "@/lib/types";

const STATUS_LABEL: Record<Exclude<RunStatus, "running">, string> = {
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

export function runStatusLabel(status: RunStatus): string {
  if (status === "running") return "Last recorded: Running";
  return STATUS_LABEL[status];
}

export function RunSummaryItem({ run }: { run: RunSummary }) {
  const prominent = run.status === "partial" || run.status === "insufficient_evidence";
  return (
    <li className="rounded border border-line bg-surface p-3">
      <Link
        href={`/research/${run.run_id}`}
        className="line-clamp-2 break-words font-medium underline"
      >
        {run.question}
      </Link>
      <p className="mt-1 text-sm text-muted">
        <span className={prominent ? "font-semibold" : undefined}>{runStatusLabel(run.status)}</span>
        {" · "}
        {formatTime(run.created_at)}
      </p>
    </li>
  );
}
