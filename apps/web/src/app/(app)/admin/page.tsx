import { OverviewPanel } from "@/components/admin/operations";

export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Overview</h1>
      <OverviewPanel />
    </div>
  );
}
