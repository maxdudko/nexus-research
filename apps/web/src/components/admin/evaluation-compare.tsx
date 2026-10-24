"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  ConfusionMatrix,
  EvaluationFacts,
  pricingLabel,
  reasonLabel,
  warningLabel,
} from "@/components/admin/evaluation-view";
import { useSessionIdentity } from "@/components/auth/session-boundary";
import { compareEvaluations } from "@/lib/admin-api";
import type { EvaluationComparison, EvaluationDeltas } from "@/lib/admin-api";
import { isApiError } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";

export function EvaluationCompare() {
  const search = useSearchParams();
  return <EvaluationCompareView leftId={search.get("left_id")} rightId={search.get("right_id")} />;
}

export function EvaluationCompareView({
  leftId,
  rightId,
}: {
  leftId: string | null;
  rightId: string | null;
}) {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const ready = Boolean(leftId && rightId);
  const router = useRouter();
  const pathname = usePathname();
  const [comparison, setComparison] = useState<EvaluationComparison | null>(null);
  const [loading, setLoading] = useState(allowed && ready);
  const [unavailable, setUnavailable] = useState(false);
  const [forbidden, setForbidden] = useState(!allowed);
  const [missing, setMissing] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    if (!allowed || !leftId || !rightId) return;
    const current = generation.current + 1;
    generation.current = current;
    let cancelled = false;
    compareEvaluations(leftId, rightId)
      .then((found) => {
        if (cancelled || current !== generation.current) return;
        setComparison(found);
        setUnavailable(false);
        setForbidden(false);
        setMissing(false);
      })
      .catch((cause: unknown) => {
        if (cancelled || current !== generation.current) return;
        if (isApiError(cause) && cause.status === 401) {
          router.replace(loginHref(pathname));
          return;
        }
        if (isApiError(cause) && cause.status === 403) {
          setForbidden(true);
          return;
        }
        if (isApiError(cause) && cause.status === 404) {
          setMissing(true);
          return;
        }
        setUnavailable(true);
      })
      .finally(() => {
        if (!cancelled && current === generation.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt, leftId, pathname, rightId, router]);

  if (!allowed || forbidden) {
    return (
      <p role="alert" className="text-sm text-danger">
        The request was rejected.
      </p>
    );
  }
  if (!ready) return <p>Choose two evaluations to compare.</p>;
  if (missing) return <p role="alert">The evaluation was not found.</p>;
  if (loading) return <p role="status">Loading comparison.</p>;
  if (unavailable || !comparison) {
    return (
      <div className="flex flex-col gap-4">
        <p role="alert" className="text-sm text-danger">
          The research service is unavailable.
        </p>
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={() => {
            setLoading(true);
            setUnavailable(false);
            setAttempt((current) => current + 1);
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {comparison.warnings.length > 0 ? (
        <ul>
          {comparison.warnings.map((warning) => (
            <li key={warning}>{warningLabel(warning)}</li>
          ))}
        </ul>
      ) : null}
      <p>{pricingLabel(comparison.cost_comparable, comparison.pricing_reason)}</p>
      {comparison.comparable ? null : (
        <div role="alert" className="flex flex-col gap-2">
          <p>These evaluations are not comparable.</p>
          <ul>
            {comparison.reasons.map((reason) => (
              <li key={reason}>{reasonLabel(reason)}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-col gap-4">
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold">Left</h2>
          <EvaluationFacts item={comparison.left} />
          {comparison.left.calibration ? (
            <ConfusionMatrix calibration={comparison.left.calibration} counts={comparison.left.counts} />
          ) : null}
        </section>
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold">Right</h2>
          <EvaluationFacts item={comparison.right} />
          {comparison.right.calibration ? (
            <ConfusionMatrix calibration={comparison.right.calibration} counts={comparison.right.counts} />
          ) : null}
        </section>
      </div>
      {comparison.deltas ? <DeltaTable deltas={comparison.deltas} /> : null}
    </div>
  );
}

function DeltaTable({ deltas }: { deltas: EvaluationDeltas }) {
  const rows = [
    ["Recall", deltas.retrieval.recall_at_k ?? "undefined"],
    ["Precision", deltas.retrieval.precision_at_k ?? "undefined"],
    ["MRR", deltas.retrieval.mrr_at_k ?? "undefined"],
    ["Defined cases", String(deltas.retrieval.defined_cases)],
    ["Citation valid", String(deltas.citations.valid)],
    ["Grounding structurally valid", String(deltas.grounding.structurally_valid)],
  ];
  return (
    <div className="overflow-x-auto">
      <table aria-label="Metric deltas" className="w-full text-sm">
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Delta</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([metric, delta]) => (
            <tr key={metric}>
              <th scope="row">{metric}</th>
              <td>{delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
