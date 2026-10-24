"use client";

import { useParams, usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConfusionMatrix, EvaluationFacts, caseText } from "@/components/admin/evaluation-view";
import { useSessionIdentity } from "@/components/auth/session-boundary";
import { getEvaluation, listEvaluationCases } from "@/lib/admin-api";
import type { EvaluationDetail as EvaluationDetailBody, StoredCase } from "@/lib/admin-api";
import { isApiError } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function EvaluationDetail() {
  const params = useParams();
  const raw = params.id;
  const evaluationId = typeof raw === "string" ? raw : "";
  return <EvaluationDetailView evaluationId={evaluationId} />;
}

export function EvaluationDetailView({ evaluationId }: { evaluationId: string }) {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const known = UUID.test(evaluationId);
  const router = useRouter();
  const pathname = usePathname();
  const [detail, setDetail] = useState<EvaluationDetailBody | null>(null);
  const [cases, setCases] = useState<StoredCase[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(allowed && known);
  const [unavailable, setUnavailable] = useState(false);
  const [forbidden, setForbidden] = useState(!allowed);
  const [missing, setMissing] = useState(!known);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    if (!allowed || !known) return;
    const current = generation.current + 1;
    generation.current = current;
    let cancelled = false;
    Promise.all([getEvaluation(evaluationId), listEvaluationCases(evaluationId)])
      .then(([found, page]) => {
        if (cancelled || current !== generation.current) return;
        setDetail(found);
        setCases(page.items);
        setCursor(page.next_cursor);
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
  }, [allowed, attempt, evaluationId, known, pathname, router]);

  async function loadOlder(): Promise<void> {
    if (!cursor) return;
    const page = await listEvaluationCases(evaluationId, 20, cursor);
    setCases((existing) => {
      const seen = new Set(existing.map((item) => `${item.category}:${item.case_id}`));
      return [...existing, ...page.items.filter((item) => !seen.has(`${item.category}:${item.case_id}`))];
    });
    setCursor(page.next_cursor);
  }

  if (!allowed || forbidden) {
    return (
      <p role="alert" className="text-sm text-danger">
        The request was rejected.
      </p>
    );
  }
  if (missing) return <p role="alert">The evaluation was not found.</p>;
  if (loading) return <p role="status">Loading evaluation.</p>;
  if (unavailable || !detail) {
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
      <EvaluationFacts item={detail} />
      <p>Dataset version {detail.dataset_version}</p>
      <p>Dataset fingerprint {detail.dataset_sha256}</p>
      <p>Report version {detail.report_version}</p>
      <p>Scorer version {detail.scorer_version}</p>
      <p>K {detail.k}</p>
      <p>{detail.code_revision ? `Code revision ${detail.code_revision}` : "Code revision unknown"}</p>
      <p>{detail.code_revision_source === "operator_asserted" ? "Operator asserted" : "Revision source unknown"}</p>
      {detail.calibration ? <ConfusionMatrix calibration={detail.calibration} counts={detail.counts} /> : null}
      <div className="overflow-x-auto">
        <table aria-label="Evaluation cases" className="w-full text-sm">
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col">Case</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((item) => (
              <tr key={`${item.category}:${item.case_id}`}>
                <td>{item.category}</td>
                <td>{item.case_id}</td>
                <td>{caseText(item)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cursor ? (
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={() => {
            void loadOlder();
          }}
        >
          Load older cases
        </button>
      ) : null}
    </div>
  );
}
