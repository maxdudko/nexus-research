import { EvaluationDetail } from "@/components/admin/evaluation-detail";

export default function EvaluationDetailPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Evaluation</h1>
      <EvaluationDetail />
    </div>
  );
}
