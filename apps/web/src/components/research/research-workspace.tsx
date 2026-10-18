"use client";

import { ResearchForm } from "@/components/research/research-form";
import { ResearchHistory } from "@/components/research/research-history";
import { ResearchProgress } from "@/components/research/research-progress";
import { useResearchStream } from "@/hooks/use-research-stream";
import type { SessionPhase } from "@/lib/research-session";

const HISTORY_REFRESH: ReadonlySet<SessionPhase> = new Set([
  "completed",
  "partial",
  "insufficient_evidence",
  "failed",
  "interrupted",
  "disconnected",
]);

export function ResearchWorkspace() {
  const stream = useResearchStream();
  const phase = stream.state.phase;
  const refreshKey = HISTORY_REFRESH.has(phase)
    ? `${phase}:${stream.state.runId ?? "none"}`
    : "draft";

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
      <ResearchHistory refreshKey={refreshKey} />
    </div>
  );
}
