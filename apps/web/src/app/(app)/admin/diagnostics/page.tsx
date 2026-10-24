import { Suspense } from "react";

import { DiagnosticsPanel } from "@/components/admin/operations";

export default function DiagnosticsPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Diagnostics</h1>
      <Suspense fallback={<p role="status">Loading diagnostics.</p>}>
        <DiagnosticsPanel />
      </Suspense>
    </div>
  );
}
