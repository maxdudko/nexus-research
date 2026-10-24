import { Suspense } from "react";

import { EvaluationCompare } from "@/components/admin/evaluation-compare";

export default function EvaluationComparePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Compare evaluations</h1>
      <Suspense fallback={<p role="status">Loading comparison.</p>}>
        <EvaluationCompare />
      </Suspense>
    </div>
  );
}
