import type { Calibration, EvaluationListItem, JudgeCounts, StoredCase } from "@/lib/admin-api";

export function provenanceBadge(item: Pick<EvaluationListItem, "suite" | "provenance_level">): string {
  if (item.suite === "offline") return "Frozen fixture regression";
  return "Informational live judge";
}

export function warningLabel(code: string): string {
  if (code === "execution_time_unknown") return "Execution time unknown";
  if (code === "code_revision_unknown") return "Code revision unknown";
  return code;
}

export function reasonLabel(code: string): string {
  const labels: Record<string, string> = {
    suite_mismatch: "Suite mismatch",
    dataset_mismatch: "Dataset mismatch",
    report_version_mismatch: "Report version mismatch",
    scorer_mismatch: "Scorer mismatch",
    k_mismatch: "K mismatch",
    judge_config_mismatch: "Judge configuration mismatch",
  };
  return labels[code] ?? code;
}

export function pricingLabel(costComparable: boolean, reason: string | null): string {
  if (costComparable) return "Pricing versions match.";
  if (reason === "pricing_version_mismatch") return "Pricing versions differ.";
  return "Pricing version unavailable.";
}

function metricText(value: string | null): string {
  return value ?? "undefined";
}

export function EvaluationFacts({ item }: { item: EvaluationListItem }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p>{provenanceBadge(item)}</p>
      {item.is_committed_baseline ? <p>Committed baseline</p> : null}
      <p>Imported {item.imported_at}</p>
      <p>{item.executed_at ?? "Execution time unknown"}</p>
      <p>
        Recall {metricText(item.retrieval.recall_at_k)}. Precision {metricText(item.retrieval.precision_at_k)}. MRR{" "}
        {metricText(item.retrieval.mrr_at_k)}.
      </p>
      <p>
        Defined cases {item.retrieval.defined_cases}. Undefined cases {item.retrieval.undefined_cases}.
      </p>
      <p>
        Citations {item.citations.valid} valid and {item.citations.invalid} invalid of {item.citations.cases}.
      </p>
      <p>
        Grounding {item.grounding.structurally_valid} structurally valid and {item.grounding.structurally_invalid}{" "}
        structurally invalid of {item.grounding.cases}.
      </p>
      {item.counts ? (
        <p>
          Compared {item.compared}. Skipped {item.counts.skipped}. Failed {item.counts.failed}.
        </p>
      ) : null}
    </div>
  );
}

export function ConfusionMatrix({
  calibration,
  counts,
}: {
  calibration: Calibration;
  counts: JudgeCounts | null;
}) {
  return (
    <div className="overflow-x-auto">
      <table aria-label="Confusion matrix" className="w-full text-sm">
        <caption>
          Gold labels are rows and predicted labels are columns. Compared {calibration.compared}.
          {counts ? ` Skipped ${counts.skipped}. Failed ${counts.failed}.` : ""}
        </caption>
        <thead>
          <tr>
            <th scope="col">Gold label</th>
            {calibration.labels.map((label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {calibration.labels.map((gold) => (
            <tr key={gold}>
              <th scope="row">{gold}</th>
              {calibration.labels.map((predicted) => (
                <td key={predicted}>{calibration.matrix[gold]?.[predicted] ?? 0}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function caseText(item: StoredCase): string {
  const result = item.result;
  if (item.category === "retrieval") {
    return `Gold ${String(result.gold_count)}. Recall ${metricText(stringOrNull(result.recall_at_k))}. Precision ${metricText(stringOrNull(result.precision_at_k))}. MRR ${metricText(stringOrNull(result.mrr_at_k))}.`;
  }
  if (item.category === "citation") {
    return `Valid ${String(result.citation_valid)}. Expected ${String(result.expect_citation_valid)}. Label ${String(result.support_label)}.`;
  }
  const predicted = "predicted_label" in result ? ` Predicted ${String(result.predicted_label)}.` : "";
  return `Structural ${String(result.structural_valid)}. Gold ${String(result.gold_label)}.${predicted}`;
}

function stringOrNull(value: string | number | boolean | null | string[] | undefined): string | null {
  return typeof value === "string" ? value : null;
}
