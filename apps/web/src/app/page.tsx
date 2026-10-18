import { ResearchWorkspace } from "@/components/research/research-workspace";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">Nexus Research</h1>
        <p className="text-sm text-neutral-700">
          Ask a question, optionally index a text PDF, and follow the run until it finishes.
        </p>
      </header>
      <ResearchWorkspace />
    </main>
  );
}
