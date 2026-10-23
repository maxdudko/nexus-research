"use client";

import { useContext, useEffect } from "react";

import { ResearchForm } from "@/components/research/research-form";
import { ResearchProgress } from "@/components/research/research-progress";
import { ResearchActivityContext } from "@/components/research/research-activity";
import { useResearchStream } from "@/hooks/use-research-stream";
import { canStop } from "@/lib/research-session";

export function ResearchWorkspace() {
  const stream = useResearchStream();
  const activity = useContext(ResearchActivityContext);
  const setBusy = activity?.setBusy;
  const registerStop = activity?.registerStop;

  useEffect(() => {
    setBusy?.(canStop(stream.state.phase));
    return () => setBusy?.(false);
  }, [setBusy, stream.state.phase]);

  useEffect(() => {
    registerStop?.(stream.stop);
    return () => registerStop?.(null);
  }, [registerStop, stream.stop]);

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
    </div>
  );
}
