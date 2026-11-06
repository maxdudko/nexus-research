import type { HTMLAttributes, ReactNode } from "react";

export type StatusTone = "neutral" | "running" | "completed" | "warning" | "danger";

const TONE: Record<string, StatusTone> = {
  completed: "completed",
  running: "running",
  partial: "warning",
  insufficient_evidence: "warning",
  failed: "danger",
  interrupted: "danger",
  idle: "neutral",
  creating_scope: "running",
  scope_failed: "danger",
  uploading: "running",
  upload_failed: "danger",
  streaming: "running",
  stalled: "warning",
  disconnected: "danger",
};

export function statusTone(status: string): StatusTone {
  return TONE[status] ?? "neutral";
}

export function StatusChip({
  tone,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone: StatusTone; children: ReactNode }) {
  return (
    <span {...props} className={["chip", `chip-${tone}`, className].filter(Boolean).join(" ")}>
      {children}
    </span>
  );
}
