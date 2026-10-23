import { ResearchHistory } from "@/components/research/research-history";

export default function HistoryPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">History</h1>
      <ResearchHistory limit={20} variant="history" />
    </div>
  );
}
