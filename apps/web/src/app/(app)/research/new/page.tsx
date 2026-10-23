import { ResearchWorkspace } from "@/components/research/research-workspace";

export default function NewResearchPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">New research</h1>
      <p className="text-sm text-muted">
        Leaving this page or closing the tab stops the request. It does not continue in the
        background.
      </p>
      <ResearchWorkspace />
    </div>
  );
}
