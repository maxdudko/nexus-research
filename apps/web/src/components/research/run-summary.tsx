"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusChip, statusTone } from "@/components/ui/status-chip";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
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

export function RunSummaryItem({
  run,
  onRemove,
}: {
  run: RunSummary;
  onRemove?: (runId: string) => Promise<void>;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmRemove(): Promise<void> {
    if (!onRemove || removing) return;
    setRemoving(true);
    setError(null);
    try {
      await onRemove(run.run_id);
      dialogRef.current?.close();
    } catch (cause: unknown) {
      setError(isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <li className="surface flex items-start justify-between gap-3 p-4">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Link
          href={`/research/${run.run_id}`}
          className="text-link line-clamp-2 break-words font-medium"
        >
          {run.question}
        </Link>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusChip tone={statusTone(run.status)}>{runStatusLabel(run.status)}</StatusChip>
          <span>
            {" · "}
            {formatTime(run.created_at)}
          </span>
        </p>
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
      </div>
      {onRemove ? (
        <>
          <Button
            type="button"
            variant="quiet"
            className="shrink-0"
            disabled={removing}
            onClick={() => dialogRef.current?.showModal()}
          >
            Remove
          </Button>
          <dialog
            ref={dialogRef}
            aria-labelledby={`remove-run-title-${run.run_id}`}
            aria-describedby={`remove-run-description-${run.run_id}`}
            className="surface m-auto w-[calc(100%_-_2rem)] max-w-md p-5 text-ink shadow-[0_18px_50px_rgb(20_20_19/0.18)] backdrop:bg-overlay"
            onClose={() => setError(null)}
          >
            <div className="flex flex-col gap-4">
              <h2
                id={`remove-run-title-${run.run_id}`}
                className="text-lg font-semibold tracking-tight"
              >
                Remove from history?
              </h2>
              <p id={`remove-run-description-${run.run_id}`}>
                This removes the saved run and its report from your history. Indexed sources are
                kept on the server. It cannot be undone.
              </p>
              {error ? (
                <p role="alert" className="text-sm text-danger">
                  {error}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  autoFocus
                  disabled={removing}
                  onClick={() => dialogRef.current?.close()}
                >
                  Keep
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  disabled={removing}
                  onClick={() => {
                    void confirmRemove();
                  }}
                >
                  {removing ? "Removing." : "Remove"}
                </Button>
              </div>
            </div>
          </dialog>
        </>
      ) : null}
    </li>
  );
}
