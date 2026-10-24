import { EvaluationHistory } from "@/components/admin/evaluation-history";

export default function EvaluationsPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Evaluations</h1>
      <EvaluationHistory />
    </div>
  );
}
