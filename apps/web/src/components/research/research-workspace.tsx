"use client";

import { ResearchForm } from "@/components/research/research-form";
import { ResearchHistory } from "@/components/research/research-history";
import { ResearchProgress } from "@/components/research/research-progress";
import { useResearchStream } from "@/hooks/use-research-stream";

export function ResearchWorkspace() {
  const stream = useResearchStream();

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
      <ResearchHistory />
    </div>
  );
}
