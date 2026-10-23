import { ResearchWorkspace } from "@/components/research/research-workspace";

export default function NewResearchPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">New research</h1>
      <ResearchWorkspace />
    </div>
  );
}
