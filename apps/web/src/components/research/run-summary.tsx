import Link from "next/link";

import { StatusChip, statusTone } from "@/components/ui/status-chip";
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
  return (
    <li className="surface flex flex-col gap-2 p-4">
      <Link
        href={`/research/${run.run_id}`}
        className="text-link line-clamp-2 break-words font-medium"
      >
        {run.question}
      </Link>
      <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
        <StatusChip tone={statusTone(run.status)}>{runStatusLabel(run.status)}</StatusChip>
        <span>{" · "}{formatTime(run.created_at)}</span>
      </p>
    </li>
  );
}
