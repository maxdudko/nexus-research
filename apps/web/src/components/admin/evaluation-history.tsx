"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { EvaluationFacts } from "@/components/admin/evaluation-view";
import { useSessionIdentity } from "@/components/auth/session-boundary";
import { listEvaluations } from "@/lib/admin-api";
import type { EvaluationListItem } from "@/lib/admin-api";
import { isApiError } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";

export function EvaluationHistory() {
  const identity = useSessionIdentity();
  const allowed = identity?.role === "admin";
  const router = useRouter();
  const pathname = usePathname();
  const [items, setItems] = useState<EvaluationListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(allowed);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [forbidden, setForbidden] = useState(!allowed);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    if (!allowed) return;
    const current = generation.current + 1;
    generation.current = current;
    let cancelled = false;
    listEvaluations()
      .then((page) => {
        if (cancelled || current !== generation.current) return;
        setItems(page.items);
        setCursor(page.next_cursor);
        setUnavailable(false);
        setForbidden(false);
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
        setUnavailable(true);
      })
      .finally(() => {
        if (!cancelled && current === generation.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt, pathname, router]);

  async function loadOlder(): Promise<void> {
    if (!cursor || loadingOlder) return;
    const current = generation.current;
    setLoadingOlder(true);
    try {
      const page = await listEvaluations(20, cursor);
      if (current !== generation.current) return;
      setItems((existing) => {
        const seen = new Set(existing.map((item) => item.id));
        return [...existing, ...page.items.filter((item) => !seen.has(item.id))];
      });
      setCursor(page.next_cursor);
      setUnavailable(false);
    } catch (cause: unknown) {
      if (current !== generation.current) return;
      if (isApiError(cause) && cause.status === 401) {
        router.replace(loginHref(pathname));
        return;
      }
      if (isApiError(cause) && cause.status === 403) {
        setForbidden(true);
        return;
      }
      setUnavailable(true);
    } finally {
      if (current === generation.current) setLoadingOlder(false);
    }
  }

  function toggle(id: string): void {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 2) return current;
      return [...current, id];
    });
  }

  if (!allowed || forbidden) {
    return (
      <p role="alert" className="text-sm text-danger">
        The request was rejected.
      </p>
    );
  }
  if (loading) return <p role="status">Loading evaluations.</p>;
  if (unavailable) {
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
  if (items.length === 0) return <p>No evaluations have been imported.</p>;

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-4">
        {items.map((item) => (
          <li key={item.id} className="flex flex-col gap-2 rounded border border-line p-3">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={selected.includes(item.id)}
                aria-label={`Select ${item.id}`}
                onChange={() => toggle(item.id)}
              />
              <Link href={`/admin/evaluations/${item.id}`} className="underline">
                Open {item.id}
              </Link>
            </label>
            <EvaluationFacts item={item} />
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2 disabled:cursor-not-allowed"
        disabled={selected.length !== 2}
        onClick={() => {
          const [left, right] = selected;
          if (!left || !right) return;
          router.push(`/admin/evaluations/compare?left_id=${left}&right_id=${right}`);
        }}
      >
        Compare
      </button>
      {cursor ? (
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={() => {
            void loadOlder();
          }}
        >
          Load older evaluations
        </button>
      ) : null}
    </div>
  );
}
