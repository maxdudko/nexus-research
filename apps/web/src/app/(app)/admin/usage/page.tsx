import { Suspense } from "react";

import { UsagePanel } from "@/components/admin/operations";

export default function UsagePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Usage</h1>
      <Suspense fallback={<p role="status">Loading usage.</p>}>
        <UsagePanel />
      </Suspense>
    </div>
  );
}
